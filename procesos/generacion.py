"""
Una generación de cartas completa para un juzgado: de la nómina subida a
los archivos de salida, dejando registro en GeneracionCartas/
ArchivoGenerado. Es el equivalente del `procesar()` de los scripts
originales — la misma secuencia (leer → duplicados → multas → agrupar →
mes predominante → filtrar por rango de días → agenda → generar cartas) —
partida en dos fases:

1. preparar() — en la petición web, unos segundos: guarda la nómina, la
   lee, agrupa y filtra, hace los controles, reserva la agenda y deja la
   corrida PENDIENTE («En cola») con todo lo que la segunda fase necesita
   en trabajo.json, junto a la nómina.
2. ejecutar() — en el worker (manage.py procesar_generaciones), sin límite
   de tiempo: arma los paquetes de cartas uno por uno, anotando el avance.
   Una nómina completa del mes son ~9.000 cartas y 15–45 minutos, mucho
   más que los 180 s que el servidor le da a una petición.

Controles de preparar():
- Un archivo de cartas no pasa de settings.JPLTAG_MAX_CARTAS_POR_ARCHIVO
  (memoria del worker).
- Patentes que ya recibieron una citación (o la sentencia) del mismo
  período: se avisa y la persona elige generar solo las que faltan o todas
  de nuevo, sin volver a subir la nómina (queda guardada, ver
  firmar_nomina_guardada).
- Con la agenda activa (settings.JPLTAG_AGENDA_ACTIVA), «Citación y
  sentencia» exige cupo para todas las patentes: sin día de audiencia no se
  puede condenar por no concurrir. Con la agenda apagada no se reservan
  cupos y todas las citaciones (también esa) salen «a coordinar».

Dos tipos de problema, a propósito tratados distinto:
- GeneracionRechazada: no se empezó. La persona ajusta algo (agrega
  bloques, cambia el paquete) y vuelve a enviar. No queda corrida
  registrada ni archivos en disco, salvo la nómina de un EnvioRepetido,
  que espera la respuesta (el worker borra las que nadie confirmó).
- La nómina en sí no sirve (sin mes reconocible, sin patentes del rango)
  o algo falla generando: queda una corrida FALLIDO con el motivo.

El worker es uno solo: procesa una corrida a la vez, en orden de llegada.
"""

import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Greatest
from django.utils import timezone

from motor.agenda import SLOT_SIN_AGENDA, asignar_agenda
from motor.agrupacion import construir_grupos, detectar_mes_predominante, filtrar_por_rango_dias
from motor.fechas_en_letras import MESES
from motor.lectura import detectar_duplicados, leer_nomina
from motor.multas import cargar_tabla_multas
from motor.plantilla import PlantillaNoDisponible, generar_cartas_certificadas
from motor.reportes import escribir_log_errores, exportar_duplicados
from nucleo.models import BloqueHorario, Juzgado, TipoCarta

from .models import ArchivoGenerado, GeneracionCartas

logger = logging.getLogger(__name__)

# El worker anota un latido en la corrida al terminar cada paquete (menos
# de 1 min con 50 cartas; un paquete se corta a los
# motor.plantilla.TIEMPO_MAXIMO_LOTE). Una corrida «en curso» sin latido por
# más que esto quedó huérfana —el worker murió a la mitad— y otro la retoma.
CORRIDA_INTERRUMPIDA_TRAS = timedelta(minutes=7)

TRABAJO = "trabajo.json"

# Estados de una corrida cuyas cartas salieron o van a salir.
ESTADOS_CON_CARTAS = (
    GeneracionCartas.Estado.OK,
    GeneracionCartas.Estado.CON_ERRORES,
    GeneracionCartas.Estado.EN_CURSO,
    GeneracionCartas.Estado.PENDIENTE,
)
ESTADOS_ACTIVOS = (GeneracionCartas.Estado.PENDIENTE, GeneracionCartas.Estado.EN_CURSO)


class GeneracionRechazada(Exception):
    """La generación no se empezó; el mensaje dice qué ajustar. `token`, si
    no está vacío, identifica la nómina que quedó guardada (sin volver a
    subirla)."""

    token = ""


class EnvioRepetido(GeneracionRechazada):
    """Hay patentes que ya recibieron carta en el mismo período. La persona
    elige qué hacer (SolicitudGeneracion.repetidas) sin volver a subir la
    nómina: `token` identifica la que quedó guardada."""

    def __init__(self, mensaje, *, n_repetidas, n_total):
        super().__init__(mensaje)
        self.n_repetidas = n_repetidas
        self.n_total = n_total


# Qué hacer con las patentes que ya recibieron carta (EnvioRepetido).
OMITIR_REPETIDAS = "omitir"
INCLUIR_REPETIDAS = "incluir"

# Una citación y una citación con sentencia son la misma citación: si la
# patente ya tiene una, la otra la cita dos veces. La sentencia va aparte.
FAMILIAS_DE_CARTA = {
    TipoCarta.CITACION: (TipoCarta.CITACION, TipoCarta.CITACION_SENTENCIA),
    TipoCarta.CITACION_SENTENCIA: (TipoCarta.CITACION, TipoCarta.CITACION_SENTENCIA),
    TipoCarta.SENTENCIA: (TipoCarta.SENTENCIA,),
}

# Una nómina guardada esperando respuesta (EnvioRepetido) vale esto; después
# el worker la borra (limpiar_carpetas_huerfanas).
VIGENCIA_NOMINA_GUARDADA = timedelta(hours=12)
_SAL_NOMINA_GUARDADA = "jpltag.nomina-guardada"
NOMINA = "nomina_original.xlsx"
TIPO_CAUSAS = "tipo_causas.xlsx"
RECLAMADA = ".en-uso"   # marca de una nómina guardada que un envío está usando


class CuposInsuficientes(Exception):
    """Un bloque cambió entre repartir los cupos y descontarlos."""


@dataclass
class SolicitudGeneracion:
    """Lo que la persona eligió en «Generar cartas»."""

    nombre_firmante: str
    cargo_firma: str
    tipo_carta: str = TipoCarta.CITACION
    fecha_sentencia: object = None
    n_cartas_paquete: int = 0
    # La nómina viene subida ahora (archivo_nomina) o ya está guardada de un
    # envío anterior que pidió respuesta (nomina_guardada: su carpeta,
    # sacada de un token verificado con leer_nomina_guardada).
    archivo_nomina: object = None
    archivo_tipo_causas: object = None
    nomina_guardada: str = ""
    nombre_nomina: str = ""
    repetidas: str = ""   # "", OMITIR_REPETIDAS o INCLUIR_REPETIDAS

    def __post_init__(self):
        if self.archivo_nomina is not None and not self.nombre_nomina:
            self.nombre_nomina = self.archivo_nomina.name


@dataclass
class _Reserva:
    """Lo que una corrida tomó de la agenda."""

    slots: dict = field(default_factory=dict)
    sin_cupo: list = field(default_factory=list)
    tomados: dict = field(default_factory=dict)        # {id del bloque: cupos}
    bloque_por_slot: dict = field(default_factory=dict)


def corrida_interrumpida(corrida):
    """Una generación «en curso» que dejó de dar señales de vida."""
    return (
        corrida.estado == GeneracionCartas.Estado.EN_CURSO
        and corrida.actualizado < timezone.now() - CORRIDA_INTERRUMPIDA_TRAS
    )


def corridas_antes_en_la_cola(corrida):
    """Cuántas generaciones (de cualquier juzgado) se procesan antes que esta."""
    if corrida.estado != GeneracionCartas.Estado.PENDIENTE:
        return 0
    return GeneracionCartas.objects.filter(
        Q(estado=GeneracionCartas.Estado.EN_CURSO)
        | Q(estado=GeneracionCartas.Estado.PENDIENTE, pk__lt=corrida.pk)
    ).count()


def mensaje_para_personas(exc):
    """Los errores de pandas/openpyxl vienen en inglés y hablan de motores
    y zips; a quien sube la nómina le sirve saber qué hacer. El detalle
    técnico queda en el log del servidor (y el texto de una excepción
    cualquiera puede traer datos de la nómina: no se muestra)."""
    if isinstance(exc, PlantillaNoDisponible):
        return str(exc)
    texto = str(exc)
    marcas_de_archivo_invalido = (
        "Excel file format cannot be determined", "File is not a zip file",
        "BadZipFile", "not a zip file", "Unsupported format", "corrupt",
    )
    if isinstance(exc, zipfile.BadZipFile) or any(m in texto for m in marcas_de_archivo_invalido):
        return (
            "El archivo no es un Excel (.xlsx) válido. Ábrelo en Excel, guárdalo como "
            "«Libro de Excel (.xlsx)» y súbelo otra vez."
        )
    return (
        "Ocurrió un error inesperado y no se generaron las cartas. Avisa al administrador "
        "del sistema: el detalle quedó en el registro del servidor."
    )


# ══════════════════════════════════════════════════════════════════════
# Agenda: reservar y devolver cupos
# ══════════════════════════════════════════════════════════════════════

def pool_de_agenda(juzgado, *, bloquear=False):
    """Arma la lista de slots disponibles (fecha dd/mm/aaaa, hora) a partir
    de los BloqueHorario vigentes de `juzgado` (de hoy en adelante; los de
    hoy, solo si su hora no pasó), repitiendo cada slot tantas veces como
    cupos_disponibles tenga su bloque (un bloque es una audiencia grupal:
    varias patentes pueden compartir la misma fecha y hora). Devuelve
    (pool_de_slots, bloque_por_slot) — lo segundo permite luego descontar los
    cupos realmente usados en esta corrida.

    Con `bloquear=True` (dentro de una transacción) las filas quedan
    bloqueadas hasta que ella termine: nadie las edita ni las toma otra
    corrida mientras se reparten."""
    consulta = juzgado.bloques_horario.vigentes().order_by("fecha", "hora")
    if bloquear:
        consulta = consulta.select_for_update(of=("self",))
    bloque_por_slot = {}
    pool = []
    for b in consulta:
        slot = (b.fecha.strftime("%d/%m/%Y"), b.hora.strftime("%H:%M"))
        bloque_por_slot[slot] = b
        pool.extend([slot] * b.cupos_disponibles)
    return pool, bloque_por_slot


def descontar_cupos_usados(slots, bloque_por_slot):
    """Después de asignar_agenda(), suma a cupos_usados de cada BloqueHorario
    las patentes que se le asignaron en esta corrida (para que la próxima no
    vuelva a ofrecerlos). Devuelve {id del bloque: cantidad}.

    Es estricto: si el bloque ya no tiene lugar (o cambió de fecha u hora),
    falla en vez de recortar en silencio, que escondería una sobreventa."""
    tomados = {}
    for slot, cantidad in _cupos_por_slot(slots.values()).items():
        bloque = bloque_por_slot.get(slot)
        if bloque is None:
            continue
        # En la base y con F(): quien edita el bloque mientras corre la
        # generación no se pisa con un valor leído antes.
        filas = BloqueHorario.objects.filter(
            pk=bloque.pk, fecha=bloque.fecha, hora=bloque.hora,
            cupos_totales__gte=F("cupos_usados") + cantidad,
        ).update(cupos_usados=F("cupos_usados") + cantidad)
        if filas != 1:
            raise CuposInsuficientes(
                f"El bloque del {slot[0]} {slot[1]} cambió mientras se repartían los horarios."
            )
        tomados[bloque.pk] = cantidad
    return tomados


def devolver_cupos(tomados):
    """Devuelve cupos a la agenda (cartas que no salieron)."""
    for pk, cantidad in tomados.items():
        BloqueHorario.objects.filter(pk=pk).update(
            cupos_usados=Greatest(F("cupos_usados") - cantidad, 0),
        )


def _cupos_por_slot(slots):
    return Counter(tuple(slot) for slot in slots if tuple(slot) != SLOT_SIN_AGENDA)


def reservar_agenda(juzgado, incluidos, crear_corrida=None, *, exigir_cupo_para_todas=False):
    """Reparte los horarios entre las patentes y descuenta los cupos, todo
    bajo el bloqueo de las filas: dos generaciones simultáneas no pueden
    repartirse el mismo cupo, y editar un bloque espera a que esto termine.

    `crear_corrida(reserva)`, si se pasa, anota la corrida en la misma
    transacción: nunca hay cupos tomados sin una corrida que los explique,
    ni siquiera si el proceso muere después.

    Con `exigir_cupo_para_todas`, si alguna patente queda sin horario no se
    reserva nada (GeneracionRechazada).
    Devuelve (_Reserva, corrida)."""
    with transaction.atomic():
        pool, bloque_por_slot = pool_de_agenda(juzgado, bloquear=True)
        slots, sin_cupo = asignar_agenda(incluidos, pool)
        if exigir_cupo_para_todas and sin_cupo:
            raise GeneracionRechazada(
                f"Faltan {len(sin_cupo)} cupos en la agenda: hay {len(pool)} libres para "
                f"{len(incluidos)} patentes. «Citación y sentencia» condena a quien no concurra "
                "el día señalado, así que cada carta necesita su día y hora. Agrega bloques en "
                "la Agenda o elige «Citación»."
            )
        tomados = descontar_cupos_usados(slots, bloque_por_slot)
        reserva = _Reserva(slots=slots, sin_cupo=sin_cupo, tomados=tomados, bloque_por_slot=bloque_por_slot)
        corrida = crear_corrida(reserva) if crear_corrida else None
    return reserva, corrida


# ══════════════════════════════════════════════════════════════════════
# Fase 1 — preparar (en la petición web)
# ══════════════════════════════════════════════════════════════════════

def _revisar_paquete(n_patentes, n_cartas_paquete):
    por_archivo = settings.JPLTAG_MAX_CARTAS_POR_ARCHIVO
    paquete = n_cartas_paquete or n_patentes
    if paquete > por_archivo:
        raise GeneracionRechazada(
            f"Son {n_patentes} cartas y un solo archivo admite hasta {por_archivo}: en "
            f"«Opciones adicionales» pon «Cartas por paquete» en {por_archivo} o menos."
        )


def patentes_ya_enviadas(juzgado, tipo_carta, anio, mes):
    """Patentes que ya recibieron carta de la misma familia (FAMILIAS_DE_CARTA)
    de este juzgado y período, en una corrida que terminó o que va a salir."""
    previas = GeneracionCartas.objects.filter(
        juzgado=juzgado, tipo_carta__in=FAMILIAS_DE_CARTA[TipoCarta(tipo_carta)],
        anio_periodo=anio, mes_periodo=mes, estado__in=ESTADOS_CON_CARTAS,
    ).only("patentes")
    ya_enviadas = set()
    for previa in previas:
        ya_enviadas.update(previa.patentes)
    return ya_enviadas


def _resolver_repetidas(juzgado, solicitud, anio, mes, incluidos):
    """Aplica la respuesta de la persona sobre las patentes que ya recibieron
    carta; sin respuesta, pregunta (EnvioRepetido). Devuelve los incluidos."""
    repetidas = patentes_ya_enviadas(juzgado, solicitud.tipo_carta, anio, mes) & set(incluidos)
    if not repetidas or solicitud.repetidas == INCLUIR_REPETIDAS:
        return incluidos
    if solicitud.repetidas == OMITIR_REPETIDAS:
        faltan = {pat: g for pat, g in incluidos.items() if pat not in repetidas}
        if not faltan:
            raise GeneracionRechazada(
                f"Las {len(incluidos)} patentes de esta nómina ya recibieron carta de "
                f"{MESES[mes - 1]} {anio} en este juzgado: no falta ninguna."
            )
        return faltan
    raise EnvioRepetido(
        f"{len(repetidas)} de las {len(incluidos)} patentes de esta nómina ya recibieron carta de "
        f"{MESES[mes - 1]} {anio} en este juzgado (por ejemplo, {', '.join(sorted(repetidas)[:5])}). "
        "Generarlas de nuevo saca dos cartas para cada una y gasta sus cupos otra vez.",
        n_repetidas=len(repetidas), n_total=len(incluidos),
    )


def firmar_nomina_guardada(carpeta_rel, juzgado, usuario, nombre_nomina):
    return signing.dumps(
        {"c": carpeta_rel, "j": juzgado.pk, "u": usuario.pk, "n": nombre_nomina}, salt=_SAL_NOMINA_GUARDADA,
    )


def leer_nomina_guardada(token, juzgado, usuario):
    """(carpeta, nombre original) de una nómina guardada por EnvioRepetido,
    si el token es auténtico, vigente, de esta persona y este juzgado, y la
    nómina sigue sin usarse. Si no, None."""
    try:
        datos = signing.loads(
            token, salt=_SAL_NOMINA_GUARDADA, max_age=VIGENCIA_NOMINA_GUARDADA.total_seconds(),
        )
    except signing.BadSignature:
        return None
    carpeta = datos.get("c", "")
    if (
        datos.get("j") != juzgado.pk or datos.get("u") != usuario.pk
        or not re.fullmatch(rf"{re.escape(juzgado.slug)}/[0-9a-f]{{32}}", carpeta)
        or not (Path(settings.SALIDAS_ROOT) / carpeta / NOMINA).is_file()
        or GeneracionCartas.objects.filter(carpeta_salida=carpeta).exists()
    ):
        return None
    return carpeta, datos.get("n", NOMINA)


def limpiar_carpetas_huerfanas(antiguedad=VIGENCIA_NOMINA_GUARDADA):
    """Borra las carpetas de SALIDAS_ROOT sin corrida que las use y más
    viejas que `antiguedad`: nóminas guardadas que nadie confirmó (traen
    datos personales). Devuelve cuántas borró."""
    raiz = Path(settings.SALIDAS_ROOT)
    usadas = set(GeneracionCartas.objects.values_list("carpeta_salida", flat=True))
    limite = (timezone.now() - antiguedad).timestamp()
    borradas = 0
    for carpeta in raiz.glob("*/*"):
        relativa = carpeta.relative_to(raiz).as_posix()
        if carpeta.is_dir() and relativa not in usadas and carpeta.stat().st_mtime < limite:
            shutil.rmtree(carpeta, ignore_errors=True)
            borradas += 1
    return borradas


def _fuera_de_todo_rango(otro_rango):
    """Patentes que no le corresponden a NINGÚN juzgado activo (un día del
    mes sin juzgado que lo procese, p. ej. el 31 si el último tramo
    termina el 30): no recibirán carta de nadie, así que van al log."""
    rangos = list(Juzgado.objects.filter(activo=True).values_list("dia_desde", "dia_hasta"))
    return {
        pat: g for pat, g in otro_rango.items()
        if not any(desde <= g["dia"] <= hasta for desde, hasta in rangos)
    }


def _reclamar(carpeta_abs):
    """Toma una nómina guardada para usarla, de forma atómica: si dos envíos
    llegan con el mismo token (doble clic), solo uno sigue."""
    try:
        os.close(os.open(carpeta_abs / RECLAMADA, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        raise GeneracionRechazada(
            "Esa nómina ya se está procesando (¿se envió dos veces?). Revisa el Inicio antes de volver a enviarla."
        ) from None
    except FileNotFoundError:
        raise GeneracionRechazada("La nómina guardada ya no está disponible: vuelve a elegir el Excel.") from None


def _guardar_subida(archivo, ruta):
    with open(ruta, "wb") as f:
        for chunk in archivo.chunks():
            f.write(chunk)
    return ruta


def _registrar_fallo(juzgado, usuario, solicitud, carpeta_rel, mensaje):
    return GeneracionCartas.objects.create(
        juzgado=juzgado, usuario=usuario,
        nombre_archivo_nomina=solicitud.nombre_nomina,
        tipo_carta=solicitud.tipo_carta, fecha_sentencia=solicitud.fecha_sentencia,
        carpeta_salida=carpeta_rel,
        estado=GeneracionCartas.Estado.FALLIDO,
        mensaje_error=mensaje,
    )


def _registrar_archivo(corrida, ruta, tipo):
    if ruta:
        ArchivoGenerado.objects.create(
            generacion=corrida, tipo=tipo,
            ruta=str(Path(ruta).relative_to(settings.SALIDAS_ROOT)),
            nombre_descarga=Path(ruta).name,
        )


def preparar(juzgado, usuario, solicitud):
    """Procesa la nómina de `solicitud` hasta dejar la corrida en cola (o
    FALLIDO si la nómina no sirve) y la devuelve. Lanza GeneracionRechazada
    si no se empezó."""
    carpeta_rel = solicitud.nomina_guardada or f"{juzgado.slug}/{uuid.uuid4().hex}"
    carpeta_abs = Path(settings.SALIDAS_ROOT) / carpeta_rel
    if solicitud.nomina_guardada:
        _reclamar(carpeta_abs)
    else:
        carpeta_abs.mkdir(parents=True, exist_ok=True)
        _guardar_subida(solicitud.archivo_nomina, carpeta_abs / NOMINA)
        if solicitud.archivo_tipo_causas:
            _guardar_subida(solicitud.archivo_tipo_causas, carpeta_abs / TIPO_CAUSAS)
    try:
        corrida = _preparar(juzgado, usuario, solicitud, carpeta_rel, carpeta_abs)
    except GeneracionRechazada as exc:
        if solicitud.nomina_guardada or isinstance(exc, EnvioRepetido):
            # Queda guardada esperando la respuesta (o que se corrija lo que
            # faltaba, p. ej. cupos): no hay que volver a subirla.
            (carpeta_abs / RECLAMADA).unlink(missing_ok=True)
            exc.token = firmar_nomina_guardada(carpeta_rel, juzgado, usuario, solicitud.nombre_nomina)
        else:
            # No se empezó: no quedan en disco los datos personales de la nómina.
            shutil.rmtree(carpeta_abs, ignore_errors=True)
        raise
    if settings.JPLTAG_GENERAR_EN_LA_PETICION and corrida.estado == GeneracionCartas.Estado.PENDIENTE:
        # Sin worker (pruebas, desarrollo local): la misma fase 2, ahora mismo.
        procesar_siguiente("en-la-peticion")
        corrida.refresh_from_db()
    return corrida


def _preparar(juzgado, usuario, solicitud, carpeta_rel, carpeta_abs):
    ruta_nomina = carpeta_abs / NOMINA
    ruta_tipo_causas = carpeta_abs / TIPO_CAUSAS if (carpeta_abs / TIPO_CAUSAS).is_file() else None
    tipo_carta = solicitud.tipo_carta
    try:
        df = leer_nomina(ruta_nomina)
        df_limpio, df_duplicados = detectar_duplicados(df)
        tabla_multas = cargar_tabla_multas(ruta_tipo_causas) if ruta_tipo_causas else []

        grupos, sin_causas, avisos = construir_grupos(df_limpio, tabla_multas, utm_multa_defecto=1)
        avisos.update(df.attrs.get("avisos_lectura", {}))
        anio_pred, mes_pred = detectar_mes_predominante(grupos)
        if not mes_pred:
            return _registrar_fallo(
                juzgado, usuario, solicitud, carpeta_rel,
                "No se pudo determinar el mes de la nómina: ninguna fila trae una "
                "fecha de infracción reconocible.",
            )

        incluidos, sin_fecha, otro_mes, otro_rango = filtrar_por_rango_dias(
            grupos, anio_pred, mes_pred, juzgado.dia_desde, juzgado.dia_hasta)
        if not incluidos:
            return _registrar_fallo(
                juzgado, usuario, solicitud, carpeta_rel,
                f"No hay ninguna patente con infracciones en los días "
                f"{juzgado.dia_desde}-{juzgado.dia_hasta} del mes predominante "
                f"({MESES[mes_pred - 1]} {anio_pred}) de esta nómina.",
            )

        # El paquete se revisa antes de preguntar por las repetidas: así no se
        # pregunta algo para rechazarlo después.
        _revisar_paquete(len(incluidos), solicitud.n_cartas_paquete)
        incluidos = _resolver_repetidas(juzgado, solicitud, anio_pred, mes_pred, incluidos)

        fuera_de_rango = _fuera_de_todo_rango(otro_rango)
        avisos = dict(avisos)
        avisos.update({
            "duplicados_excluidos": len(df_duplicados),
            "otro_rango_de_dias": len(otro_rango),
            "fuera_de_todo_rango": len(fuera_de_rango),
        })
        base_reporte = str(carpeta_abs / "reporte")
        ruta_dup = exportar_duplicados(base_reporte, df_duplicados)

        def _encolar(reserva):
            """Dentro de la transacción de la reserva: la corrida nace «En
            cola» con su trabajo y sus reportes ya en disco, o no nace."""
            ruta_log = escribir_log_errores(
                base_reporte, sin_fecha, otro_mes, sin_causas, reserva.sin_cupo, grupos,
                fuera_de_rango=fuera_de_rango,
            )
            _escribir_trabajo(carpeta_abs, incluidos, reserva.slots, solicitud)
            corrida = GeneracionCartas.objects.create(
                juzgado=juzgado, usuario=usuario,
                nombre_archivo_nomina=solicitud.nombre_nomina,
                tipo_carta=tipo_carta, fecha_sentencia=solicitud.fecha_sentencia,
                anio_periodo=anio_pred, mes_periodo=mes_pred,
                total_patentes=len(incluidos), total_sin_cupo_agenda=len(reserva.sin_cupo),
                total_duplicados=len(df_duplicados), total_sin_causas_validas=len(sin_causas),
                carpeta_salida=carpeta_rel,
                patentes=sorted(incluidos),
                cupos_por_bloque={str(pk): n for pk, n in reserva.tomados.items()},
                avisos={**avisos, "sin_cupo_agenda": len(reserva.sin_cupo)},
                estado=GeneracionCartas.Estado.PENDIENTE,
            )
            _registrar_archivo(corrida, ruta_dup, ArchivoGenerado.Tipo.DUPLICADOS)
            _registrar_archivo(corrida, ruta_log, ArchivoGenerado.Tipo.LOG_ERRORES)
            return corrida

        if tipo_carta == TipoCarta.SENTENCIA or not settings.JPLTAG_AGENDA_ACTIVA:
            # La sentencia ya está dictada: no cita a ninguna audiencia. Con
            # la agenda apagada, toda citación sale «a coordinar». En ambos
            # casos no se asigna agenda ni se gastan cupos.
            if tipo_carta != TipoCarta.SENTENCIA:
                avisos["agenda_desactivada"] = True
            with transaction.atomic():
                return _encolar(_Reserva())
        _, corrida = reservar_agenda(
            juzgado, incluidos, _encolar,
            exigir_cupo_para_todas=(tipo_carta == TipoCarta.CITACION_SENTENCIA),
        )
        return corrida
    except GeneracionRechazada:
        raise
    except Exception as exc:
        # La reserva y la corrida van en la misma transacción: si algo falló,
        # no quedó ningún cupo tomado.
        logger.exception("Falló la preparación de cartas (%s)", juzgado.slug)
        return _registrar_fallo(juzgado, usuario, solicitud, carpeta_rel, mensaje_para_personas(exc))


def _escribir_trabajo(carpeta_abs, incluidos, slots, solicitud):
    """Lo que el worker necesita para armar las cartas, sin volver a leer la
    nómina. Trae datos personales: vive junto a la nómina, en SALIDAS_ROOT."""
    trabajo = {
        "incluidos": incluidos,
        "slots": slots,
        "nombre_firmante": solicitud.nombre_firmante,
        "cargo_firma": solicitud.cargo_firma,
        "fecha_sentencia": solicitud.fecha_sentencia.isoformat() if solicitud.fecha_sentencia else None,
        "n_cartas_paquete": solicitud.n_cartas_paquete,
    }
    temporal = carpeta_abs / f"{TRABAJO}.tmp"
    temporal.write_text(json.dumps(trabajo, ensure_ascii=False), encoding="utf-8")
    temporal.replace(carpeta_abs / TRABAJO)


def _leer_trabajo(carpeta_abs):
    trabajo = json.loads((carpeta_abs / TRABAJO).read_text(encoding="utf-8"))
    trabajo["slots"] = {pat: tuple(slot) for pat, slot in trabajo["slots"].items()}
    if trabajo["fecha_sentencia"]:
        trabajo["fecha_sentencia"] = date.fromisoformat(trabajo["fecha_sentencia"])
    return trabajo


# ══════════════════════════════════════════════════════════════════════
# Fase 2 — ejecutar (en el worker)
# ══════════════════════════════════════════════════════════════════════

class Detenido(Exception):
    """El worker recibió la orden de detenerse (SIGTERM): la corrida vuelve a
    la cola y se retoma desde el último paquete terminado."""


class CorridaPerdida(Exception):
    """La corrida dejó de ser de este worker: alguien la canceló o devolvió
    sus cupos, u otro worker la retomó por falta de latido."""


def tomar_siguiente(worker):
    """La corrida más antigua en cola, ya marcada EN_CURSO y a nombre de
    `worker`, o None. También retoma una «en curso» sin latido (su worker
    murió): sigue desde el último paquete terminado, con los mismos cupos."""
    with transaction.atomic():
        corrida = (
            GeneracionCartas.objects.select_for_update(skip_locked=True)
            .filter(
                Q(estado=GeneracionCartas.Estado.PENDIENTE)
                | Q(estado=GeneracionCartas.Estado.EN_CURSO,
                    actualizado__lt=timezone.now() - CORRIDA_INTERRUMPIDA_TRAS)
            )
            .order_by("pk").first()
        )
        if corrida is None:
            return None
        corrida.estado = GeneracionCartas.Estado.EN_CURSO
        corrida.worker = worker
        corrida.save(update_fields=["estado", "worker", "actualizado"])
    return corrida


def procesar_siguiente(worker, al_latir=None):
    """Toma y genera la siguiente corrida en cola. Devuelve la corrida
    procesada, o None si no había nada. `al_latir()` se llama después de
    cada paquete: el worker anota ahí su latido y, si debe detenerse, lanza
    Detenido."""
    corrida = tomar_siguiente(worker)
    if corrida is not None:
        ejecutar(corrida, al_latir=al_latir)
    return corrida


def cancelar(corrida_id, juzgado, motivo):
    """Saca de la cola una corrida que no ha empezado, o una «en curso» sin
    latido, y devuelve sus cupos. Devuelve la corrida, o None si ya no se
    puede (terminó, o un worker la está generando)."""
    with transaction.atomic():
        corrida = (
            GeneracionCartas.objects.select_for_update()
            .filter(pk=corrida_id, juzgado=juzgado, estado__in=ESTADOS_ACTIVOS).first()
        )
        if corrida is None or (
            corrida.estado == GeneracionCartas.Estado.EN_CURSO and not corrida_interrumpida(corrida)
        ):
            return None
        tomados = {int(pk): n for pk, n in corrida.cupos_por_bloque.items()}
        devolver_cupos(tomados)
        corrida.estado = GeneracionCartas.Estado.FALLIDO
        corrida.mensaje_error = motivo
        corrida.cupos_por_bloque = {}
        corrida.patentes = []
        corrida.save(update_fields=["estado", "mensaje_error", "cupos_por_bloque", "patentes", "actualizado"])
    _limpiar_trabajo(corrida, quitar_cartas=True)
    logger.warning("Generación %s (%s) cancelada; cupos devueltos: %s", corrida.pk, juzgado.slug, tomados)
    corrida.cupos_devueltos = sum(tomados.values())
    return corrida


def _config_motor(juzgado, trabajo, tipo_carta):
    fecha = trabajo["fecha_sentencia"]
    return {
        "tipo_carta": str(tipo_carta),
        "fecha_audiencia": fecha.strftime("%d/%m/%Y") if fecha else "",
        "slug_juzgado": juzgado.slug,
        "nombre_juzgado": juzgado.nombre,
        "utm_valor": juzgado.utm_valor,
        "direccion_juzgado": juzgado.direccion,
        "max_causas_por_pagina": juzgado.max_causas_por_pagina,
        "nombre_firmante": trabajo["nombre_firmante"],
        "cargo_firma": trabajo["cargo_firma"],
        "logo_path": juzgado.logo.path if juzgado.logo else None,
        "sello_path": juzgado.sello_judicial.path if juzgado.sello_judicial else None,
        "firma_path": juzgado.firma.path if juzgado.firma else None,
        "plantillas_word": {
            tipo: juzgado.plantilla_propia(tipo).path
            for tipo in TipoCarta.values if juzgado.plantilla_propia(tipo)
        },
    }


def _cupos_de_patentes(slots, bloques_tomados, patentes):
    """{id del bloque: cupos} que la reserva le dio a esas patentes.
    `bloques_tomados` es {(fecha, hora): id del bloque} de esta corrida."""
    cupos = Counter()
    for slot, cantidad in _cupos_por_slot(slots.get(p, SLOT_SIN_AGENDA) for p in patentes).items():
        if slot in bloques_tomados:
            cupos[bloques_tomados[slot]] += cantidad
    return dict(cupos)


def _bloques_tomados(corrida):
    ids = [int(pk) for pk in corrida.cupos_por_bloque]
    return {
        (b.fecha.strftime("%d/%m/%Y"), b.hora.strftime("%H:%M")): b.pk
        for b in BloqueHorario.objects.filter(pk__in=ids)
    }


def _limpiar_trabajo(corrida, *, quitar_cartas=False):
    """Al cerrar: trabajo.json (copia de todos los datos de la nómina) ya no
    sirve; y si las cartas no salieron, tampoco sus archivos."""
    carpeta_abs = Path(settings.SALIDAS_ROOT) / corrida.carpeta_salida
    (carpeta_abs / TRABAJO).unlink(missing_ok=True)
    if quitar_cartas:
        shutil.rmtree(carpeta_abs / "cartas", ignore_errors=True)


def _es_mia(consulta, corrida):
    return consulta.filter(pk=corrida.pk, estado=GeneracionCartas.Estado.EN_CURSO, worker=corrida.worker)


def _cerrar(corrida, *, devolver=None, archivos=(), **campos):
    """Guarda el resultado, devuelve cupos y registra los archivos en una
    sola transacción, y solo si la corrida sigue siendo de este worker."""
    with transaction.atomic():
        actual = _es_mia(GeneracionCartas.objects.select_for_update(), corrida).first()
        if actual is None:
            logger.warning("La generación %s ya no es de este worker; no se cierra", corrida.pk)
            return False
        for nombre, valor in campos.items():
            setattr(actual, nombre, valor)
        actual.save()
        devolver_cupos(devolver or {})
        for ruta, tipo in archivos:
            _registrar_archivo(actual, ruta, tipo)
    corrida.refresh_from_db()
    _limpiar_trabajo(corrida, quitar_cartas=corrida.estado == GeneracionCartas.Estado.FALLIDO)
    return True


def ejecutar(corrida, al_latir=None):
    """Genera los paquetes de cartas de una corrida ya tomada (EN_CURSO) por
    `corrida.worker`, desde el primer paquete que falte."""
    juzgado = corrida.juzgado
    carpeta_abs = Path(settings.SALIDAS_ROOT) / corrida.carpeta_salida
    tomados = Counter({int(pk): n for pk, n in corrida.cupos_por_bloque.items()})
    try:
        trabajo = _leer_trabajo(carpeta_abs)
        incluidos = trabajo["incluidos"]
        paquete = trabajo["n_cartas_paquete"] or len(incluidos)
        total_lotes = -(-len(incluidos) // paquete)
        _es_mia(GeneracionCartas.objects, corrida).update(lotes_total=total_lotes, actualizado=timezone.now())

        def _latido(lotes_hechos):
            if not _es_mia(GeneracionCartas.objects, corrida).update(
                lotes_hechos=lotes_hechos, actualizado=timezone.now(),
            ):
                raise CorridaPerdida(corrida.pk)
            if al_latir:
                al_latir()

        resultado = generar_cartas_certificadas(
            incluidos, juzgado.slug, trabajo["slots"], trabajo["n_cartas_paquete"],
            _config_motor(juzgado, trabajo, corrida.tipo_carta),
            carpeta_salida=str(carpeta_abs / "cartas"), al_terminar_lote=_latido,
            aislar_lotes=True, lotes_listos=corrida.lotes_hechos,
        )
    except Detenido:
        # Vuelve a la cola tal como está: el próximo worker sigue desde lotes_hechos.
        _es_mia(GeneracionCartas.objects, corrida).update(estado=GeneracionCartas.Estado.PENDIENTE, worker="")
        logger.info("Generación %s devuelta a la cola al detener el worker", corrida.pk)
        return corrida
    except CorridaPerdida:
        logger.warning("La generación %s dejó de ser de este worker; se deja de generar", corrida.pk)
        corrida.refresh_from_db()
        if corrida.estado == GeneracionCartas.Estado.FALLIDO:
            # La cancelaron mientras se generaba: lo que alcanzó a escribir sobra.
            _limpiar_trabajo(corrida, quitar_cartas=True)
        return corrida
    except Exception as exc:
        logger.exception("Falló la generación de cartas %s (%s)", corrida.pk, juzgado.slug)
        _cerrar(
            corrida, devolver=dict(tomados),
            estado=GeneracionCartas.Estado.FALLIDO, mensaje_error=mensaje_para_personas(exc),
            cupos_por_bloque={}, patentes=[],
        )
        return corrida

    # Las cartas de un paquete que falló no salieron: sus cupos vuelven.
    devueltos = _cupos_de_patentes(trabajo["slots"], _bloques_tomados(corrida), resultado["patentes_fallidas"])
    tomados.subtract(devueltos)
    avisos = dict(corrida.avisos)
    if resultado["errores"]:
        avisos["errores_generando_lotes"] = resultado["errores"]
    _cerrar(
        corrida, devolver=devueltos,
        archivos=[(r, ArchivoGenerado.Tipo.CARTAS) for r in resultado["archivos"]]
        + [(resultado["seguimiento"], ArchivoGenerado.Tipo.SEGUIMIENTO)],
        estado=GeneracionCartas.Estado.CON_ERRORES if resultado["errores"] else GeneracionCartas.Estado.OK,
        avisos=avisos,
        patentes=sorted(set(incluidos) - set(resultado["patentes_fallidas"])),
        cupos_por_bloque={str(pk): n for pk, n in tomados.items() if n > 0},
        lotes_hechos=total_lotes,
    )
    return corrida

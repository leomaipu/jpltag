"""
Una generación de cartas completa para un juzgado: de la nómina subida a
los archivos de salida, dejando registro en GeneracionCartas/
ArchivoGenerado. Es el equivalente del `procesar()` de los scripts
originales — la misma secuencia (leer → duplicados → multas → agrupar →
mes predominante → filtrar por rango de días → agenda → generar cartas) —
más los controles que la web necesita y la terminal no:

- Topes de tamaño (settings.JPLTAG_MAX_CARTAS_POR_CORRIDA y
  JPLTAG_MAX_CARTAS_POR_ARCHIVO): la generación corre dentro de la propia
  petición web, que el servidor corta a los 180 s.
- Una generación a la vez en todo el sitio: el contenedor tiene 3 workers
  y 1 GB de memoria.
- Patentes que ya recibieron esa misma carta en el mismo período: se
  avisa y hay que confirmarlo (cada reenvío gasta cupos y duplica cartas).
- «Citación y sentencia» exige cupo de agenda para todas las patentes: sin
  día de audiencia no se puede condenar por no concurrir.

Dos tipos de problema, a propósito tratados distinto:
- GeneracionRechazada: no se empezó. La persona ajusta algo (divide la
  nómina, agrega bloques, espera, confirma) y vuelve a enviar. No queda
  corrida registrada ni archivos en disco.
- La nómina en sí no sirve (sin mes reconocible, sin patentes del rango)
  o algo falla generando: queda una corrida FALLIDO con el motivo, igual
  que antes.
"""

import logging
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from motor.agenda import SLOT_SIN_AGENDA, asignar_agenda
from motor.agrupacion import construir_grupos, detectar_mes_predominante, filtrar_por_rango_dias
from motor.fechas_en_letras import MESES
from motor.lectura import detectar_duplicados, leer_nomina
from motor.multas import cargar_tabla_multas
from motor.plantilla import generar_cartas_certificadas
from motor.reportes import escribir_log_errores, exportar_duplicados
from nucleo.models import BloqueHorario, Juzgado, TipoCarta

from .models import ArchivoGenerado, GeneracionCartas

logger = logging.getLogger(__name__)

# Una generación que sigue «en curso» después de esto se da por
# interrumpida (el proceso murió: tiempo agotado, falta de memoria,
# reinicio). Con los topes de tamaño una corrida real termina en ~3 min.
CORRIDA_INTERRUMPIDA_TRAS = timedelta(minutes=10)

# Estados de una corrida cuyas cartas salieron (o pueden estar saliendo).
ESTADOS_CON_CARTAS = (
    GeneracionCartas.Estado.OK,
    GeneracionCartas.Estado.CON_ERRORES,
    GeneracionCartas.Estado.EN_CURSO,
)


class GeneracionRechazada(Exception):
    """La generación no se empezó; el mensaje dice qué ajustar."""


class EnvioRepetido(GeneracionRechazada):
    """Hay patentes que ya recibieron esta carta en el mismo período. Se
    puede seguir confirmándolo (SolicitudGeneracion.confirmar_repeticion)."""


class CuposInsuficientes(Exception):
    """Un bloque cambió entre repartir los cupos y descontarlos."""


@dataclass
class SolicitudGeneracion:
    """Lo que la persona eligió en «Generar cartas»."""

    archivo_nomina: object
    nombre_firmante: str
    cargo_firma: str
    tipo_carta: str = TipoCarta.CITACION
    fecha_sentencia: object = None
    archivo_tipo_causas: object = None
    n_cartas_paquete: int = 0
    confirmar_repeticion: bool = False


@dataclass
class _Reserva:
    """Lo que una corrida tomó de la agenda."""

    slots: dict = field(default_factory=dict)
    sin_cupo: list = field(default_factory=list)
    tomados: dict = field(default_factory=dict)        # {id del bloque: cupos}
    bloque_por_slot: dict = field(default_factory=dict)


def corrida_interrumpida(corrida):
    """Una generación que sigue «en curso» mucho después de lo que tarda."""
    return (
        corrida.estado == GeneracionCartas.Estado.EN_CURSO
        and corrida.creado < timezone.now() - CORRIDA_INTERRUMPIDA_TRAS
    )


def mensaje_para_personas(exc):
    """Los errores de pandas/openpyxl vienen en inglés y hablan de motores
    y zips; a quien sube la nómina le sirve saber qué hacer. El detalle
    técnico queda en el log del servidor."""
    import zipfile

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
    return texto


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
    return Counter(slot for slot in slots if slot != SLOT_SIN_AGENDA)


def reservar_agenda(juzgado, incluidos, crear_corrida=None, *, exigir_cupo_para_todas=False):
    """Reparte los horarios entre las patentes y descuenta los cupos, todo
    bajo el bloqueo de las filas: dos generaciones simultáneas no pueden
    repartirse el mismo cupo, y editar un bloque espera a que esto termine.

    `crear_corrida(tomados, n_sin_cupo)`, si se pasa, anota la corrida
    EN_CURSO en la misma transacción: nunca hay cupos tomados sin una
    corrida que los explique, ni siquiera si el proceso muere después.

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
        corrida = crear_corrida(tomados, len(sin_cupo)) if crear_corrida else None
    reserva = _Reserva(slots=slots, sin_cupo=sin_cupo, tomados=tomados, bloque_por_slot=bloque_por_slot)
    return reserva, corrida


def _cupos_de_patentes(reserva, patentes):
    """{id del bloque: cupos} que la reserva le dio a esas patentes."""
    por_slot = _cupos_por_slot(reserva.slots.get(p, SLOT_SIN_AGENDA) for p in patentes)
    cupos = Counter()
    for slot, cantidad in por_slot.items():
        bloque = reserva.bloque_por_slot.get(slot)
        if bloque is not None and bloque.pk in reserva.tomados:
            cupos[bloque.pk] += cantidad
    return dict(cupos)


# ══════════════════════════════════════════════════════════════════════
# Controles previos
# ══════════════════════════════════════════════════════════════════════

def _revisar_tamano(n_patentes, n_cartas_paquete):
    maximo = settings.JPLTAG_MAX_CARTAS_POR_CORRIDA
    por_archivo = settings.JPLTAG_MAX_CARTAS_POR_ARCHIVO
    if n_patentes > maximo:
        raise GeneracionRechazada(
            f"Esta nómina da {n_patentes} cartas para este juzgado y el máximo por generación "
            f"es {maximo}: el servidor corta las generaciones que pasan de 3 minutos. Divide el "
            f"Excel en partes de hasta {maximo} patentes, dejando todas las filas de una misma "
            "patente en la misma parte, y genera cada una por separado."
        )
    paquete = n_cartas_paquete or n_patentes
    if paquete > por_archivo:
        raise GeneracionRechazada(
            f"Son {n_patentes} cartas y un solo archivo admite hasta {por_archivo}: en "
            f"«Opciones adicionales» pon «Cartas por paquete» en {por_archivo} o menos."
        )


def _revisar_repetidas(juzgado, tipo_carta, anio, mes, patentes):
    """Patentes de `patentes` que ya recibieron esta carta de este juzgado
    en este período, en una corrida que terminó (o sigue en marcha)."""
    previas = GeneracionCartas.objects.filter(
        juzgado=juzgado, tipo_carta=tipo_carta, anio_periodo=anio, mes_periodo=mes,
        estado__in=ESTADOS_CON_CARTAS,
    ).only("patentes")
    ya_enviadas = set()
    for previa in previas:
        ya_enviadas.update(previa.patentes)
    repetidas = ya_enviadas & set(patentes)
    if repetidas:
        etiqueta = TipoCarta(tipo_carta).label.lower()
        raise EnvioRepetido(
            f"{len(repetidas)} de estas {len(patentes)} patentes ya recibieron carta de "
            f"{etiqueta} de {MESES[mes - 1]} {anio} en este juzgado (por ejemplo, "
            f"{', '.join(sorted(repetidas)[:5])}). Si generas de nuevo, saldrán dos cartas "
            "para cada una y se gastarán cupos otra vez."
        )


def _revisar_que_no_haya_otra_en_curso():
    """Se llama dentro de la transacción que anota la corrida, con las filas
    de Juzgado bloqueadas: dos personas que generan a la vez no pasan las
    dos este control."""
    list(Juzgado.objects.select_for_update().order_by("pk").values_list("pk", flat=True))
    en_curso = (
        GeneracionCartas.objects.filter(
            estado=GeneracionCartas.Estado.EN_CURSO,
            creado__gte=timezone.now() - CORRIDA_INTERRUMPIDA_TRAS,
        ).select_related("juzgado").first()
    )
    if en_curso is not None:
        raise GeneracionRechazada(
            f"Ahora mismo se están generando cartas del {en_curso.juzgado.nombre} "
            f"(empezó a las {timezone.localtime(en_curso.creado):%H:%M}). Para no dejar al servidor "
            "sin memoria se genera una tanda a la vez: espera a que termine y vuelve a enviar."
        )


def _fuera_de_todo_rango(otro_rango):
    """Patentes que no le corresponden a NINGÚN juzgado activo (un día del
    mes sin juzgado que lo procese, p. ej. el 31 si el último tramo
    termina el 30): no recibirán carta de nadie, así que van al log."""
    rangos = list(Juzgado.objects.filter(activo=True).values_list("dia_desde", "dia_hasta"))
    return {
        pat: g for pat, g in otro_rango.items()
        if not any(desde <= g["dia"] <= hasta for desde, hasta in rangos)
    }


# ══════════════════════════════════════════════════════════════════════
# La generación
# ══════════════════════════════════════════════════════════════════════

def _guardar_subida(archivo, ruta):
    with open(ruta, "wb") as f:
        for chunk in archivo.chunks():
            f.write(chunk)
    return ruta


def _config_motor(juzgado, solicitud):
    return {
        "tipo_carta": str(solicitud.tipo_carta),
        "fecha_audiencia": solicitud.fecha_sentencia.strftime("%d/%m/%Y") if solicitud.fecha_sentencia else "",
        "slug_juzgado": juzgado.slug,
        "nombre_juzgado": juzgado.nombre,
        "utm_valor": juzgado.utm_valor,
        "direccion_juzgado": juzgado.direccion,
        "max_causas_por_pagina": juzgado.max_causas_por_pagina,
        "nombre_firmante": solicitud.nombre_firmante,
        "cargo_firma": solicitud.cargo_firma,
        "logo_path": juzgado.logo.path if juzgado.logo else None,
        "sello_path": juzgado.sello_judicial.path if juzgado.sello_judicial else None,
        "firma_path": juzgado.firma.path if juzgado.firma else None,
        "plantillas_word": {
            tipo: juzgado.plantilla_propia(tipo).path
            for tipo in TipoCarta.values if juzgado.plantilla_propia(tipo)
        },
    }


def _registrar_fallo(juzgado, usuario, solicitud, carpeta_rel, mensaje):
    return GeneracionCartas.objects.create(
        juzgado=juzgado, usuario=usuario,
        nombre_archivo_nomina=solicitud.archivo_nomina.name,
        tipo_carta=solicitud.tipo_carta, fecha_sentencia=solicitud.fecha_sentencia,
        carpeta_salida=carpeta_rel,
        estado=GeneracionCartas.Estado.FALLIDO,
        mensaje_error=mensaje,
    )


def generar(juzgado, usuario, solicitud):
    """Procesa la nómina de `solicitud` para `juzgado` y devuelve la
    GeneracionCartas resultante. Lanza GeneracionRechazada si no se empezó."""
    carpeta_rel = f"{juzgado.slug}/{uuid.uuid4().hex}"
    carpeta_abs = Path(settings.SALIDAS_ROOT) / carpeta_rel
    carpeta_abs.mkdir(parents=True, exist_ok=True)
    try:
        return _generar(juzgado, usuario, solicitud, carpeta_rel, carpeta_abs)
    except GeneracionRechazada:
        # No se empezó: no quedan en disco los datos personales de la nómina.
        shutil.rmtree(carpeta_abs, ignore_errors=True)
        raise


def _generar(juzgado, usuario, solicitud, carpeta_rel, carpeta_abs):
    ruta_nomina = _guardar_subida(solicitud.archivo_nomina, carpeta_abs / "nomina_original.xlsx")
    ruta_tipo_causas = (
        _guardar_subida(solicitud.archivo_tipo_causas, carpeta_abs / "tipo_causas.xlsx")
        if solicitud.archivo_tipo_causas else None
    )
    tipo_carta = solicitud.tipo_carta

    reserva = _Reserva()
    tomados = Counter()    # lo que esta corrida le tiene quitado a la agenda
    corrida = None         # la GeneracionCartas EN_CURSO
    try:
        df = leer_nomina(ruta_nomina)
        df_limpio, df_duplicados = detectar_duplicados(df)
        tabla_multas = cargar_tabla_multas(ruta_tipo_causas) if ruta_tipo_causas else []

        grupos, sin_causas, avisos = construir_grupos(df_limpio, tabla_multas, utm_multa_defecto=1)
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

        _revisar_tamano(len(incluidos), solicitud.n_cartas_paquete)
        if not solicitud.confirmar_repeticion:
            _revisar_repetidas(juzgado, tipo_carta, anio_pred, mes_pred, incluidos)

        def _anotar_corrida(tomados, n_sin_cupo):
            _revisar_que_no_haya_otra_en_curso()
            return GeneracionCartas.objects.create(
                juzgado=juzgado, usuario=usuario,
                nombre_archivo_nomina=solicitud.archivo_nomina.name,
                tipo_carta=tipo_carta, fecha_sentencia=solicitud.fecha_sentencia,
                anio_periodo=anio_pred, mes_periodo=mes_pred,
                total_patentes=len(incluidos), total_sin_cupo_agenda=n_sin_cupo,
                carpeta_salida=carpeta_rel,
                patentes=sorted(incluidos),
                cupos_por_bloque={str(pk): n for pk, n in tomados.items()},
                estado=GeneracionCartas.Estado.EN_CURSO,
            )

        if tipo_carta == TipoCarta.SENTENCIA:
            # La sentencia ya está dictada: no se cita a ninguna audiencia,
            # así que no se asigna agenda ni se gastan cupos.
            with transaction.atomic():
                corrida = _anotar_corrida({}, 0)
        else:
            reserva, corrida = reservar_agenda(
                juzgado, incluidos, _anotar_corrida,
                exigir_cupo_para_todas=(tipo_carta == TipoCarta.CITACION_SENTENCIA),
            )
            tomados.update(reserva.tomados)

        resultado = generar_cartas_certificadas(
            incluidos, juzgado.slug, reserva.slots, solicitud.n_cartas_paquete,
            _config_motor(juzgado, solicitud), carpeta_salida=str(carpeta_abs / "cartas"),
        )

        # Las cartas de un lote que falló no salieron: sus cupos vuelven.
        devueltos = _cupos_de_patentes(reserva, resultado["patentes_fallidas"])
        devolver_cupos(devueltos)
        tomados.subtract(devueltos)

        fuera_de_rango = _fuera_de_todo_rango(otro_rango)
        base_reporte = str(carpeta_abs / "reporte")
        ruta_dup = exportar_duplicados(base_reporte, df_duplicados)
        ruta_log = escribir_log_errores(
            base_reporte, sin_fecha, otro_mes, sin_causas, reserva.sin_cupo, grupos,
            fuera_de_rango=fuera_de_rango,
        )

        avisos = dict(avisos)
        avisos.update({
            "duplicados_excluidos": len(df_duplicados),
            "otro_rango_de_dias": len(otro_rango),
            "fuera_de_todo_rango": len(fuera_de_rango),
            "sin_cupo_agenda": len(reserva.sin_cupo),
        })
        if resultado["errores"]:
            avisos["errores_generando_lotes"] = resultado["errores"]

        corrida.avisos = avisos
        corrida.total_duplicados = len(df_duplicados)
        corrida.total_sin_causas_validas = len(sin_causas)
        corrida.patentes = sorted(set(incluidos) - set(resultado["patentes_fallidas"]))
        corrida.cupos_por_bloque = {str(pk): n for pk, n in tomados.items() if n > 0}
        corrida.estado = (
            GeneracionCartas.Estado.CON_ERRORES if resultado["errores"] else GeneracionCartas.Estado.OK
        )
        corrida.save()

        def _registrar_archivo(ruta, tipo):
            if ruta:
                ArchivoGenerado.objects.create(
                    generacion=corrida, tipo=tipo,
                    ruta=str(Path(ruta).relative_to(settings.SALIDAS_ROOT)),
                    nombre_descarga=Path(ruta).name,
                )

        for ruta in resultado["archivos"]:
            _registrar_archivo(ruta, ArchivoGenerado.Tipo.CARTAS)
        _registrar_archivo(resultado["seguimiento"], ArchivoGenerado.Tipo.SEGUIMIENTO)
        _registrar_archivo(ruta_dup, ArchivoGenerado.Tipo.DUPLICADOS)
        _registrar_archivo(ruta_log, ArchivoGenerado.Tipo.LOG_ERRORES)
        return corrida

    except GeneracionRechazada:
        raise
    except Exception as exc:
        logger.exception("Falló la generación de cartas (%s)", juzgado.slug)
        # Si no salió ninguna carta, los cupos reservados vuelven a la agenda.
        devolver_cupos({pk: n for pk, n in tomados.items() if n > 0})
        if corrida is not None:
            corrida.estado = GeneracionCartas.Estado.FALLIDO
            corrida.mensaje_error = mensaje_para_personas(exc)
            corrida.cupos_por_bloque = {}
            corrida.patentes = []
            corrida.save(update_fields=["estado", "mensaje_error", "cupos_por_bloque", "patentes"])
            return corrida
        return _registrar_fallo(juzgado, usuario, solicitud, carpeta_rel, mensaje_para_personas(exc))

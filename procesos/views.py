"""
Vistas web: ingresar, panel, subir nómina (dispara el motor completo),
ver resultado, descargar archivo, y agenda de audiencias.

`_procesar_nomina` es el equivalente del `procesar()` de los scripts
originales — la misma secuencia (leer → duplicados → multas → agrupar →
mes predominante → filtrar por rango de días → agenda → generar cartas),
pero disparada desde un formulario web en vez de la terminal, y dejando
registro en GeneracionCartas/ArchivoGenerado en vez de solo imprimir en
pantalla.
"""

import logging
import re
import zipfile
from collections import Counter
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from motor.agenda import SLOT_SIN_AGENDA, asignar_agenda
from motor.agrupacion import construir_grupos, detectar_mes_predominante, filtrar_por_rango_dias
from motor.cartas import fmt_pesos
from motor.fechas_en_letras import MESES
from motor.lectura import detectar_duplicados, leer_nomina
from motor.multas import cargar_tabla_multas
from motor.plantilla import generar_cartas_certificadas, ruta_plantilla_defecto
from motor.reportes import escribir_log_errores, exportar_duplicados
from motor.utm import ErrorConsultaUTM, consultar_utm_vigente
from nucleo.models import BloqueAjuste, BloqueHorario, Juzgado, TipoCarta, ahora_local

from .forms import CUPOS_MAXIMOS, BloqueEditarForm, BloqueHorarioForm, BloqueSerieForm, IngresarForm, PlantillaJuzgadoForm, SubirNominaForm
from .models import ArchivoGenerado, GeneracionCartas
from .permisos import (
    PerfilRequeridoMixin,
    PuedeEditarAgendaMixin,
    PuedeEditarPlantillaMixin,
    PuedeGenerarCartasMixin,
)


logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Helpers de juzgado activo (un Administrador sin juzgado fijo ve los 3 y
# elige con cuál trabajar vía ?juzgado=<slug>)
# ══════════════════════════════════════════════════════════════════════

def _mensaje_para_personas(exc):
    """Los errores de pandas/openpyxl vienen en inglés y hablan de motores
    y zips; a quien sube la nómina le sirve saber qué hacer. El detalle
    técnico queda en el log del servidor."""
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


def _juzgados_visibles(perfil):
    if perfil.juzgado_id:
        return Juzgado.objects.filter(pk=perfil.juzgado_id)
    return Juzgado.objects.filter(activo=True)


def _juzgado_actual(request, perfil):
    visibles = _juzgados_visibles(perfil)
    slug = request.GET.get("juzgado") or request.POST.get("juzgado")
    if slug:
        if not re.fullmatch(r"[-\w]{1,50}", slug):
            raise Http404("Juzgado no encontrado.")
        if not visibles.filter(slug=slug).exists() and Juzgado.objects.filter(slug=slug).exists():
            # El juzgado existe pero no es del usuario: 403, igual que el
            # resto de los recursos de otro juzgado (no 404, que sugiere
            # que no existe).
            raise PermissionDenied("No tienes acceso a ese juzgado.")
        return get_object_or_404(visibles, slug=slug)
    return visibles.first()


# ══════════════════════════════════════════════════════════════════════
# Ingreso / panel
# ══════════════════════════════════════════════════════════════════════

class IngresarView(LoginView):
    template_name = "procesos/login.html"
    authentication_form = IngresarForm
    redirect_authenticated_user = True


class PanelView(PerfilRequeridoMixin, View):
    def get(self, request):
        perfil = request.user.perfil
        juzgados = _juzgados_visibles(perfil)
        generaciones = (
            GeneracionCartas.objects.filter(juzgado__in=juzgados)
            .select_related("juzgado", "usuario")[:10]
        )
        proximos_bloques = (
            BloqueHorario.objects.filter(juzgado__in=juzgados).vigentes()
            .select_related("juzgado")
            .order_by("fecha", "hora")[:8]
        )
        return render(request, "procesos/panel.html", {
            "perfil": perfil,
            "juzgados": juzgados,
            "generaciones": generaciones,
            "proximos_bloques": proximos_bloques,
        })


# ══════════════════════════════════════════════════════════════════════
# Subir nómina → generar cartas
# ══════════════════════════════════════════════════════════════════════

class SubirNominaView(PuedeGenerarCartasMixin, View):
    def get(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "No hay ningún juzgado configurado todavía.")
            return redirect("procesos:panel")
        form = SubirNominaForm(initial={"nombre_firmante": request.user.get_full_name()})
        return render(request, "procesos/subir_nomina.html", {
            "form": form, "juzgado": juzgado, "juzgados": _juzgados_visibles(perfil),
        })

    def post(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "No hay ningún juzgado configurado todavía.")
            return redirect("procesos:panel")

        form = SubirNominaForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "procesos/subir_nomina.html", {
                "form": form, "juzgado": juzgado, "juzgados": _juzgados_visibles(perfil),
            })

        datos = form.cleaned_data
        generacion = _procesar_nomina(
            juzgado=juzgado,
            usuario=request.user,
            archivo_nomina=datos["nomina"],
            archivo_tipo_causas=datos.get("tipo_causas"),
            tipo_carta=datos["tipo_carta"],
            fecha_sentencia=datos.get("fecha_audiencia"),
            nombre_firmante=datos.get("nombre_firmante") or "",
            cargo_firma=datos.get("cargo_firma") or "SECRETARIO",
            n_cartas_paquete=datos.get("n_cartas_paquete") or 0,
        )
        return redirect("procesos:resultado", pk=generacion.pk)


def _registrar_fallo(juzgado, usuario, nombre_archivo, carpeta_rel, mensaje):
    return GeneracionCartas.objects.create(
        juzgado=juzgado, usuario=usuario,
        nombre_archivo_nomina=nombre_archivo,
        carpeta_salida=carpeta_rel,
        estado=GeneracionCartas.Estado.FALLIDO,
        mensaje_error=mensaje,
    )


def _pool_de_agenda(juzgado, *, bloquear=False):
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


class CuposInsuficientes(Exception):
    """Un bloque cambió entre repartir los cupos y descontarlos."""


def _descontar_cupos_usados(slots, bloque_por_slot):
    """Después de asignar_agenda(), suma a cupos_usados de cada BloqueHorario
    las patentes que se le asignaron en esta corrida (para que la próxima no
    vuelva a ofrecerlos). Devuelve {id del bloque: cantidad}.

    Es estricto: si el bloque ya no tiene lugar (o cambió de fecha u hora),
    falla en vez de recortar en silencio, que escondería una sobreventa."""
    asignados = [slot for slot in slots.values() if slot != SLOT_SIN_AGENDA]
    tomados = {}
    for slot, cantidad in Counter(asignados).items():
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


def _reservar_agenda(juzgado, incluidos, crear_corrida=None):
    """Reparte los horarios entre las patentes y descuenta los cupos, todo
    bajo el bloqueo de las filas: dos generaciones simultáneas no pueden
    repartirse el mismo cupo, y editar un bloque espera a que esto termine.

    `crear_corrida(tomados)`, si se pasa, anota la corrida EN_CURSO en la
    misma transacción: nunca hay cupos tomados sin una corrida que los
    explique, ni siquiera si el proceso muere después.
    Devuelve (slots, sin_cupo, {id del bloque: cupos tomados}, corrida)."""
    with transaction.atomic():
        pool, bloque_por_slot = _pool_de_agenda(juzgado, bloquear=True)
        slots, sin_cupo = asignar_agenda(incluidos, pool)
        tomados = _descontar_cupos_usados(slots, bloque_por_slot)
        corrida = crear_corrida(tomados, len(sin_cupo)) if crear_corrida else None
    return slots, sin_cupo, tomados, corrida


def _devolver_cupos(tomados):
    """Deshace una reserva cuando la generación falló: ninguna carta salió."""
    for pk, cantidad in tomados.items():
        BloqueHorario.objects.filter(pk=pk).update(
            cupos_usados=Greatest(F("cupos_usados") - cantidad, 0),
        )


def _procesar_nomina(*, juzgado, usuario, archivo_nomina, archivo_tipo_causas,
                      nombre_firmante, cargo_firma, n_cartas_paquete,
                      tipo_carta=TipoCarta.CITACION, fecha_sentencia=None):
    import uuid

    carpeta_rel = f"{juzgado.slug}/{uuid.uuid4().hex}"
    carpeta_abs = Path(settings.SALIDAS_ROOT) / carpeta_rel
    carpeta_abs.mkdir(parents=True, exist_ok=True)

    ruta_nomina = carpeta_abs / "nomina_original.xlsx"
    with open(ruta_nomina, "wb") as f:
        for chunk in archivo_nomina.chunks():
            f.write(chunk)

    ruta_tipo_causas = None
    if archivo_tipo_causas:
        ruta_tipo_causas = carpeta_abs / "tipo_causas.xlsx"
        with open(ruta_tipo_causas, "wb") as f:
            for chunk in archivo_tipo_causas.chunks():
                f.write(chunk)

    cupos_tomados = {}   # lo que esta corrida le quitó a la agenda
    corrida = None       # la GeneracionCartas EN_CURSO, si se reservaron cupos
    try:
        df = leer_nomina(ruta_nomina)
        df_limpio, df_duplicados = detectar_duplicados(df)
        tabla_multas = cargar_tabla_multas(ruta_tipo_causas) if ruta_tipo_causas else []

        grupos, sin_causas, avisos = construir_grupos(df_limpio, tabla_multas, utm_multa_defecto=1)
        anio_pred, mes_pred = detectar_mes_predominante(grupos)

        if not mes_pred:
            return _registrar_fallo(
                juzgado, usuario, archivo_nomina.name, carpeta_rel,
                "No se pudo determinar el mes de la nómina: ninguna fila trae una "
                "fecha de infracción reconocible.",
            )

        incluidos, sin_fecha, otro_mes, otro_rango = filtrar_por_rango_dias(
            grupos, anio_pred, mes_pred, juzgado.dia_desde, juzgado.dia_hasta)

        if not incluidos:
            return _registrar_fallo(
                juzgado, usuario, archivo_nomina.name, carpeta_rel,
                f"No hay ninguna patente con infracciones en los días "
                f"{juzgado.dia_desde}-{juzgado.dia_hasta} del mes predominante "
                f"({MESES[mes_pred - 1]} {anio_pred}) de esta nómina.",
            )

        if tipo_carta == TipoCarta.SENTENCIA:
            # La sentencia ya está dictada: no se cita a ninguna audiencia,
            # así que no se asigna agenda ni se gastan cupos.
            slots, sin_cupo = {}, []
        else:
            def _anotar_corrida(tomados, n_sin_cupo):
                return GeneracionCartas.objects.create(
                    juzgado=juzgado, usuario=usuario,
                    nombre_archivo_nomina=archivo_nomina.name,
                    tipo_carta=tipo_carta, fecha_sentencia=fecha_sentencia,
                    anio_periodo=anio_pred, mes_periodo=mes_pred,
                    total_patentes=len(incluidos), total_sin_cupo_agenda=n_sin_cupo,
                    carpeta_salida=carpeta_rel,
                    cupos_por_bloque={str(pk): n for pk, n in tomados.items()},
                    estado=GeneracionCartas.Estado.EN_CURSO,
                )

            slots, sin_cupo, cupos_tomados, corrida = _reservar_agenda(juzgado, incluidos, _anotar_corrida)

        config = {
            "tipo_carta": str(tipo_carta),
            "fecha_audiencia": fecha_sentencia.strftime("%d/%m/%Y") if fecha_sentencia else "",
            "slug_juzgado": juzgado.slug,
            "nombre_juzgado": juzgado.nombre,
            "utm_valor": juzgado.utm_valor,
            "direccion_juzgado": juzgado.direccion,
            "max_causas_por_pagina": juzgado.max_causas_por_pagina,
            "nombre_firmante": nombre_firmante,
            "cargo_firma": cargo_firma,
            "logo_path": juzgado.logo.path if juzgado.logo else None,
            "sello_path": juzgado.sello_judicial.path if juzgado.sello_judicial else None,
            "firma_path": juzgado.firma.path if juzgado.firma else None,
            "plantillas_word": {
                tipo: juzgado.plantilla_propia(tipo).path
                for tipo in TipoCarta.values if juzgado.plantilla_propia(tipo)
            },
        }

        carpeta_cartas = carpeta_abs / "cartas"
        resultado = generar_cartas_certificadas(
            incluidos, juzgado.slug, slots, n_cartas_paquete, config,
            carpeta_salida=str(carpeta_cartas),
        )

        base_reporte = str(carpeta_abs / "reporte")
        ruta_dup = exportar_duplicados(base_reporte, df_duplicados)
        ruta_log = escribir_log_errores(base_reporte, sin_fecha, otro_mes, sin_causas, sin_cupo, grupos)

        avisos_completos = dict(avisos)
        avisos_completos.update({
            "duplicados_excluidos": len(df_duplicados),
            "otro_rango_de_dias": len(otro_rango),
            "sin_cupo_agenda": len(sin_cupo),
        })
        if resultado["errores"]:
            avisos_completos["errores_generando_lotes"] = resultado["errores"]

        estado = (
            GeneracionCartas.Estado.OK if not resultado["errores"]
            else GeneracionCartas.Estado.CON_ERRORES
        )

        campos = dict(
            juzgado=juzgado, usuario=usuario,
            nombre_archivo_nomina=archivo_nomina.name,
            tipo_carta=tipo_carta, fecha_sentencia=fecha_sentencia,
            anio_periodo=anio_pred, mes_periodo=mes_pred,
            total_patentes=len(incluidos),
            total_duplicados=len(df_duplicados),
            total_sin_causas_validas=len(sin_causas),
            total_sin_cupo_agenda=len(sin_cupo),
            carpeta_salida=carpeta_rel,
            avisos=avisos_completos,
            cupos_por_bloque={str(pk): n for pk, n in cupos_tomados.items()},
            estado=estado,
        )
        if corrida is not None:
            for nombre, valor in campos.items():
                setattr(corrida, nombre, valor)
            corrida.save()
            generacion = corrida
        else:
            generacion = GeneracionCartas.objects.create(**campos)

        def _registrar_archivo(ruta, tipo):
            if not ruta:
                return
            ArchivoGenerado.objects.create(
                generacion=generacion, tipo=tipo,
                ruta=str(Path(ruta).relative_to(settings.SALIDAS_ROOT)),
                nombre_descarga=Path(ruta).name,
            )

        for ruta in resultado["archivos"]:
            _registrar_archivo(ruta, ArchivoGenerado.Tipo.CARTAS)
        _registrar_archivo(resultado["seguimiento"], ArchivoGenerado.Tipo.SEGUIMIENTO)
        _registrar_archivo(ruta_dup, ArchivoGenerado.Tipo.DUPLICADOS)
        _registrar_archivo(ruta_log, ArchivoGenerado.Tipo.LOG_ERRORES)

        return generacion

    except Exception as exc:
        logger.exception("Falló la generación de cartas (%s)", juzgado.slug)
        # Si no salió ninguna carta, los cupos reservados vuelven a la agenda.
        _devolver_cupos(cupos_tomados)
        if corrida is not None:
            corrida.estado = GeneracionCartas.Estado.FALLIDO
            corrida.mensaje_error = _mensaje_para_personas(exc)
            corrida.cupos_por_bloque = {}
            corrida.save(update_fields=["estado", "mensaje_error", "cupos_por_bloque"])
            return corrida
        return _registrar_fallo(
            juzgado, usuario, archivo_nomina.name, carpeta_rel, _mensaje_para_personas(exc),
        )


# ══════════════════════════════════════════════════════════════════════
# Resultado y descarga
# ══════════════════════════════════════════════════════════════════════

class ResultadoView(PuedeGenerarCartasMixin, View):
    def get(self, request, pk):
        perfil = request.user.perfil
        generacion = get_object_or_404(
            GeneracionCartas.objects.select_related("juzgado", "usuario"), pk=pk,
        )
        if not perfil.tiene_acceso_a(generacion.juzgado):
            raise PermissionDenied("No tienes acceso a este juzgado.")
        archivos = generacion.archivos.all()
        mes_nombre = MESES[generacion.mes_periodo - 1] if generacion.mes_periodo else None
        return render(request, "procesos/resultado.html", {
            "generacion": generacion, "archivos": archivos, "mes_nombre": mes_nombre,
        })


class DescargarArchivoView(PuedeGenerarCartasMixin, View):
    def get(self, request, pk):
        perfil = request.user.perfil
        archivo = get_object_or_404(
            ArchivoGenerado.objects.select_related("generacion__juzgado"), pk=pk,
        )
        if not perfil.tiene_acceso_a(archivo.generacion.juzgado):
            raise PermissionDenied("No tienes acceso a este juzgado.")
        ruta_abs = Path(settings.SALIDAS_ROOT) / archivo.ruta
        if not ruta_abs.exists():
            raise Http404("El archivo ya no está disponible.")
        return FileResponse(open(ruta_abs, "rb"), as_attachment=True, filename=archivo.nombre_descarga)


# ══════════════════════════════════════════════════════════════════════
# Agenda de audiencias (Módulo 3 — Agenda con cupos)
# ══════════════════════════════════════════════════════════════════════

# TODO(permisos-agenda): post() revisa perfil.puede_editar_agenda() a mano
# en vez de heredar de PuedeEditarAgendaMixin (procesos/permisos.py), que
# existe pero no se usa en ninguna vista. Sin consecuencias hoy (los 4
# roles pueden editar la agenda), pero si eso cambia conviene migrar esta
# vista a ese mixin — ver la nota junto a PuedeEditarAgendaMixin.
HISTORIAL_VISIBLE = 60


def _contexto_agenda(perfil, juzgado, form=None, serie_form=None, historial_completo=False):
    """Datos de /agenda/: los bloques que aún no empiezan (los que ofrece la
    generación de cartas) y, aparte, el historial de los que ya pasaron."""
    ahora = ahora_local()
    todos = BloqueHorario.objects.filter(juzgado=juzgado) if juzgado else BloqueHorario.objects.none()
    puede_editar = perfil.puede_editar_agenda()
    pasados = todos.pasados(ahora).order_by("-fecha", "-hora")
    total_pasados = pasados.count()
    return {
        "perfil": perfil, "juzgados": _juzgados_visibles(perfil), "juzgado": juzgado,
        "proximos": todos.vigentes(ahora).order_by("fecha", "hora"),
        "pasados": pasados if historial_completo else pasados[:HISTORIAL_VISIBLE],
        "total_pasados": total_pasados,
        "historial_recortado": not historial_completo and total_pasados > HISTORIAL_VISIBLE,
        "historial_visible": HISTORIAL_VISIBLE,
        "hoy": ahora.date(),
        "puede_editar": puede_editar,
        "form": (form or BloqueHorarioForm(juzgado=juzgado)) if puede_editar else None,
        "serie_form": (serie_form or BloqueSerieForm()) if puede_editar else None,
    }


def _etiqueta_bloque(bloque):
    """«mié 14 oct, 09:00» para los mensajes."""
    from django.utils.formats import date_format
    return f"{date_format(bloque.fecha, 'D j M').lower()}, {bloque.hora:%H:%M}"


class AgendaView(PerfilRequeridoMixin, View):
    def get(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        contexto = _contexto_agenda(
            perfil, juzgado, historial_completo=request.GET.get("historial") == "todo",
        )
        if contexto["form"] is not None and request.GET.get("abrir") == "uno":
            # Viene de «crear un bloque nuevo» en un bloque con cartas.
            contexto["abrir"] = "uno"
            try:
                cupos = int(request.GET.get("cupos", ""))
            except ValueError:
                cupos = None
            if cupos and 1 <= cupos <= CUPOS_MAXIMOS:
                contexto["form"] = BloqueHorarioForm(juzgado=juzgado, initial={"cupos_totales": cupos})
                contexto["form"].fields["fecha"].widget.attrs["autofocus"] = True
        return render(request, "procesos/agenda.html", contexto)

    def post(self, request):
        perfil = request.user.perfil
        if not perfil.puede_editar_agenda():
            raise PermissionDenied("Tu rol no puede editar la agenda.")
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "Elige un juzgado antes de agregar un bloque.")
            return redirect("procesos:agenda")
        volver = f"{reverse('procesos:agenda')}?juzgado={juzgado.slug}"

        if request.POST.get("accion") == "serie":
            serie = BloqueSerieForm(request.POST)
            if not serie.is_valid():
                contexto = _contexto_agenda(perfil, juzgado, serie_form=serie)
                contexto["abrir"] = "serie"
                return render(request, "procesos/agenda.html", contexto)
            datos = serie.cleaned_data
            horas = serie.horas()
            nuevos = 0
            with transaction.atomic():
                for hora in horas:
                    _, creado = BloqueHorario.objects.get_or_create(
                        juzgado=juzgado, fecha=datos["fecha"], hora=hora,
                        defaults={"cupos_totales": datos["cupos_totales"]},
                    )
                    nuevos += creado
            repetidos = len(horas) - nuevos
            dia = date_format_corto(datos["fecha"])
            if nuevos:
                texto = f"Se agregaron {nuevos} bloque{'s' if nuevos != 1 else ''} el {dia}, de {horas[0]:%H:%M} a {horas[-1]:%H:%M}."
                if repetidos:
                    texto += f" {repetidos} ya existía{'n' if repetidos != 1 else ''} y no se tocó."
                messages.success(request, texto)
            else:
                messages.info(request, f"Ya existían los {len(horas)} bloques de esa serie el {dia}; no se agregó ninguno.")
            return redirect(volver)

        form = BloqueHorarioForm(request.POST, juzgado=juzgado)
        if form.is_valid():
            bloque = form.save(commit=False)
            bloque.juzgado = juzgado
            try:
                with transaction.atomic():
                    bloque.save()
                messages.success(request, f"Bloque del {_etiqueta_bloque(bloque)} agregado, con {bloque.cupos_totales} cupos.")
            except IntegrityError:
                messages.error(request, "Ya existe un bloque para esa fecha y hora en este juzgado.")
            return redirect(volver)
        contexto = _contexto_agenda(perfil, juzgado, form=form)
        contexto["abrir"] = "uno"
        return render(request, "procesos/agenda.html", contexto)


def date_format_corto(fecha):
    from django.utils.formats import date_format
    return date_format(fecha, "D j M").lower()


class BloqueNoExiste(Exception):
    """El bloque se eliminó (p. ej. doble clic en «Eliminar», o la pantalla
    quedó abierta en otra pestaña)."""


class _AvisaSiElBloqueNoExiste:
    """En vez de un 404 pelado, vuelve a la agenda y explica."""

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except BloqueNoExiste:
            messages.info(request, "Ese bloque ya no existe: alguien lo eliminó.")
            return redirect("procesos:agenda")


def _bloque_del_usuario(request, pk, *, bloquear=False):
    """El bloque `pk`, si la persona tiene acceso a su juzgado (403 si no,
    igual que el resto de los recursos de otro juzgado)."""
    consulta = BloqueHorario.objects.select_related("juzgado")
    if bloquear:
        consulta = consulta.select_for_update(of=("self",))
    try:
        bloque = consulta.get(pk=pk)
    except BloqueHorario.DoesNotExist:
        raise BloqueNoExiste(pk) from None
    if not request.user.perfil.tiene_acceso_a(bloque.juzgado):
        raise PermissionDenied("No tienes acceso al juzgado de este bloque.")
    return bloque


def _volver_a_la_agenda(bloque):
    return f"{reverse('procesos:agenda')}?juzgado={bloque.juzgado.slug}#bloque-{bloque.pk}"


CORRIDA_INTERRUMPIDA_TRAS = timedelta(minutes=10)


def _corrida_interrumpida(corrida):
    """Una generación que sigue «en curso» mucho después de lo que tarda
    (el proceso murió: tiempo agotado, falta de memoria, reinicio)."""
    return (
        corrida.estado == GeneracionCartas.Estado.EN_CURSO
        and corrida.creado < timezone.now() - CORRIDA_INTERRUMPIDA_TRAS
    )


def _historia_del_bloque(bloque):
    """Qué corridas de cartas tomaron cupos de este bloque y qué correcciones
    manuales tuvo: de dónde sale cada número de «cupos ocupados»."""
    corridas = list(
        GeneracionCartas.objects
        .filter(juzgado=bloque.juzgado, cupos_por_bloque__has_key=str(bloque.pk))
        .select_related("usuario").order_by("-creado")[:20]
    )
    for c in corridas:
        c.cupos_en_este_bloque = c.cupos_por_bloque.get(str(bloque.pk), 0)
        c.interrumpida = _corrida_interrumpida(c)
    # La suma sale de todas las corridas, no solo de las 20 que se muestran.
    registrados = sum(
        c.cupos_por_bloque.get(str(bloque.pk), 0)
        for c in GeneracionCartas.objects.filter(
            juzgado=bloque.juzgado, cupos_por_bloque__has_key=str(bloque.pk),
        ).only("cupos_por_bloque")
    )
    return {
        "corridas": corridas,
        "ajustes": list(bloque.ajustes.select_related("usuario")[:20]),
        # Cupos que no se pueden atribuir a una corrida: de antes de que se
        # anotara, o cambiados por una corrección.
        "sin_origen": max(bloque.cupos_usados - registrados, 0),
    }


class BloqueEditarView(PuedeEditarAgendaMixin, _AvisaSiElBloqueNoExiste, View):
    """Editar un bloque de horario: fecha, hora y cupos, más «Cerrar cupos»
    y la corrección de cupos ocupados. Las reglas están en BloqueEditarForm."""

    def _pasado(self, request, bloque):
        if bloque.ya_paso:
            messages.info(request, "Ese bloque ya pasó: queda en el historial y no se puede cambiar.")
            return redirect(f"{reverse('procesos:agenda')}?juzgado={bloque.juzgado.slug}")
        return None

    def _pantalla(self, request, bloque, form):
        return render(request, "procesos/bloque_editar.html", {
            "bloque": bloque, "juzgado": bloque.juzgado, "form": form,
            "puede_eliminar": bloque.cupos_usados == 0,
            **_historia_del_bloque(bloque),
        })

    def _devolver_corrida(self, request, bloque):
        """Devuelve a la agenda los cupos de una generación interrumpida."""
        try:
            pk = int(request.POST.get("corrida", ""))
        except ValueError:
            pk = None
        corrida = (
            GeneracionCartas.objects.select_for_update()
            .filter(pk=pk, juzgado=bloque.juzgado, estado=GeneracionCartas.Estado.EN_CURSO).first()
        )
        if corrida is None or not _corrida_interrumpida(corrida):
            messages.error(request, "Esa generación no se puede devolver: ya terminó o sigue en marcha.")
            return redirect("procesos:bloque_editar", pk=bloque.pk)
        tomados = {int(k): v for k, v in corrida.cupos_por_bloque.items()}
        _devolver_cupos(tomados)
        corrida.estado = GeneracionCartas.Estado.FALLIDO
        corrida.mensaje_error = "Se interrumpió antes de terminar. Sus cupos se devolvieron a la agenda."
        corrida.cupos_por_bloque = {}
        corrida.save(update_fields=["estado", "mensaje_error", "cupos_por_bloque"])
        logger.warning(
            "Cupos devueltos de la generación interrumpida %s (%s): %s por %s",
            corrida.pk, bloque.juzgado.slug, tomados, request.user.get_username(),
        )
        messages.success(
            request,
            f"Se devolvieron {sum(tomados.values())} cupos a la agenda. Las cartas de esa generación no salieron.",
        )
        return redirect("procesos:bloque_editar", pk=bloque.pk)

    def get(self, request, pk):
        bloque = _bloque_del_usuario(request, pk)
        return self._pasado(request, bloque) or self._pantalla(
            request, bloque, BloqueEditarForm(instance=bloque, juzgado=bloque.juzgado),
        )

    def post(self, request, pk):
        _bloque_del_usuario(request, pk)  # 403/404 antes de bloquear la fila
        with transaction.atomic():
            # La fila se bloquea: una generación de cartas en curso también
            # actualiza los cupos ocupados.
            bloque = _bloque_del_usuario(request, pk, bloquear=True)
            pasado = self._pasado(request, bloque)
            if pasado:
                return pasado

            if request.POST.get("accion") == "devolver_corrida":
                return self._devolver_corrida(request, bloque)

            if request.POST.get("accion") == "cerrar":
                if bloque.cupos_usados == 0:
                    messages.info(
                        request,
                        "Este bloque no tiene cartas: no hay nada que cerrar. Si ya no lo necesitas, elimínalo.",
                    )
                    return redirect("procesos:bloque_editar", pk=bloque.pk)
                bloque.cupos_totales = bloque.cupos_usados
                bloque.save(update_fields=["cupos_totales"])
                messages.success(
                    request,
                    f"Bloque del {_etiqueta_bloque(bloque)} cerrado: quedó con {bloque.cupos_usados} de "
                    f"{bloque.cupos_totales} cupos y no recibirá más cartas.",
                )
                return redirect(_volver_a_la_agenda(bloque))

            form = BloqueEditarForm(request.POST, instance=bloque, juzgado=bloque.juzgado)
            if form.is_valid():
                usados_antes = bloque.cupos_usados
                bloque = form.save(commit=False)
                bloque.cupos_usados = form.cleaned_data["cupos_usados"]
                try:
                    with transaction.atomic():
                        bloque.save(update_fields=["fecha", "hora", "cupos_totales", "cupos_usados"])
                except IntegrityError:
                    form.add_error("hora", "Ya hay un bloque a esa hora ese día.")
                else:
                    if bloque.cupos_usados != usados_antes:
                        BloqueAjuste.objects.create(
                            juzgado=bloque.juzgado, bloque=bloque, fecha=bloque.fecha, hora=bloque.hora,
                            usuario=request.user, nombre_usuario=request.user.get_username(),
                            desde=usados_antes, hasta=bloque.cupos_usados,
                            motivo=form.cleaned_data["motivo_correccion"].strip(),
                        )
                        logger.warning(
                            "Cupos ocupados corregidos a mano: bloque %s (%s) de %s a %s por %s",
                            bloque.pk, bloque.juzgado.slug, usados_antes, bloque.cupos_usados,
                            request.user.get_username(),
                        )
                    messages.success(request, f"Bloque del {_etiqueta_bloque(bloque)} actualizado.")
                    return redirect(_volver_a_la_agenda(bloque))
            bloque.refresh_from_db()
            if form.cambio_concurrente:
                # Pantalla nueva, con los valores de ahora: reenviar la vieja
                # repetiría el mismo choque.
                for e in form.non_field_errors():
                    messages.error(request, e)
                return redirect("procesos:bloque_editar", pk=bloque.pk)
            return self._pantalla(request, bloque, form)


class BloqueEliminarView(PuedeEditarAgendaMixin, _AvisaSiElBloqueNoExiste, View):
    """Eliminar un bloque, con una pantalla de confirmación. Solo si ninguna
    carta tomó cupos de él: si no, esas cartas citarían a un horario que ya
    no existe en la agenda."""

    def get(self, request, pk):
        bloque = _bloque_del_usuario(request, pk)
        if bloque.ya_paso:
            messages.info(request, "Ese bloque ya pasó: queda en el historial y no se puede eliminar.")
            return redirect(f"{reverse('procesos:agenda')}?juzgado={bloque.juzgado.slug}")
        if bloque.cupos_usados:
            messages.error(
                request,
                f"No se puede eliminar: {bloque.cupos_usados} cartas ya citan a este horario. "
                "Puedes cerrar sus cupos para que no reciba más.",
            )
            return redirect("procesos:bloque_editar", pk=bloque.pk)
        return render(request, "procesos/bloque_eliminar.html", {"bloque": bloque, "juzgado": bloque.juzgado})

    def post(self, request, pk):
        _bloque_del_usuario(request, pk)
        with transaction.atomic():
            bloque = _bloque_del_usuario(request, pk, bloquear=True)
            if bloque.cupos_usados:
                messages.error(
                    request,
                    f"No se puede eliminar: {bloque.cupos_usados} cartas ya citan a este horario.",
                )
                return redirect("procesos:bloque_editar", pk=bloque.pk)
            etiqueta, slug = _etiqueta_bloque(bloque), bloque.juzgado.slug
            bloque.delete()
        messages.success(request, f"Bloque del {etiqueta} eliminado.")
        return redirect(f"{reverse('procesos:agenda')}?juzgado={slug}")


# ══════════════════════════════════════════════════════════════════════
# Plantilla y logos del Juzgado (Módulo 1 — plantilla Word editable,
# Módulo 2 — logo/sello/firma propios, sin pasar por /admin/)
# ══════════════════════════════════════════════════════════════════════

def _contexto_plantilla(perfil, juzgado, form):
    """Datos de la pantalla /plantilla/: por cada tipo de carta, el campo del
    formulario y si el Juzgado usa su plantilla propia o la del repo."""
    plantillas = []
    for tipo in TipoCarta:
        campo = Juzgado.CAMPO_PLANTILLA[tipo]
        propia = juzgado.plantilla_propia(tipo)
        plantillas.append({
            "tipo": tipo.value,
            "etiqueta": tipo.label,
            "campo": form[campo],
            "nombre_propia": Path(propia.name).name if propia else None,
            "hay_defecto": ruta_plantilla_defecto(juzgado.slug, juzgado.nombre, tipo.value) is not None,
        })
    return {
        "form": form, "juzgado": juzgado, "juzgados": _juzgados_visibles(perfil),
        "plantillas": plantillas,
        "utm_actual": fmt_pesos(juzgado.utm_valor),
    }


class PlantillaView(PuedeEditarPlantillaMixin, View):
    def get(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "No hay ningún juzgado configurado todavía.")
            return redirect("procesos:panel")
        form = PlantillaJuzgadoForm(instance=juzgado)
        return render(request, "procesos/plantilla.html", _contexto_plantilla(perfil, juzgado, form))

    def post(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "No hay ningún juzgado configurado todavía.")
            return redirect("procesos:panel")

        form = PlantillaJuzgadoForm(request.POST, request.FILES, instance=juzgado)
        if form.is_valid():
            form.save()
            messages.success(request, "Cambios guardados.")
            return redirect(f"{reverse('procesos:plantilla')}?juzgado={juzgado.slug}")

        return render(request, "procesos/plantilla.html", _contexto_plantilla(perfil, juzgado, form))


class DescargarPlantillaView(PuedeEditarPlantillaMixin, View):
    """Baja la plantilla que se usa hoy para un tipo de carta: la propia del
    Juzgado si la subió, o la del repo. Sirve de punto de partida para
    editar el texto sin armarla de cero."""

    def get(self, request, pk, tipo):
        perfil = request.user.perfil
        juzgado = get_object_or_404(Juzgado, pk=pk)
        if not perfil.tiene_acceso_a(juzgado):
            raise PermissionDenied("No tienes acceso a este juzgado.")
        if tipo not in TipoCarta.values:
            raise Http404("Tipo de carta no reconocido.")
        propia = juzgado.plantilla_propia(tipo)
        ruta = propia.path if propia else ruta_plantilla_defecto(juzgado.slug, juzgado.nombre, tipo)
        if not ruta or not Path(ruta).exists():
            raise Http404("Este juzgado todavía no tiene plantilla para ese tipo de carta.")
        return FileResponse(
            open(ruta, "rb"), as_attachment=True,
            filename=f"plantilla_{tipo.lower()}_{juzgado.slug}.docx",
        )


class VerImagenJuzgadoView(PuedeEditarPlantillaMixin, View):
    """Vista previa de logo/sello/firma dentro de la propia app — en
    producción MEDIA_URL no se sirve por HTTP (ver DESPLIEGUE.md), así
    que /admin/ no puede mostrar una vista previa de estas imágenes;
    esta vista sí, porque lee el archivo del disco con el mismo control
    de acceso por Juzgado que el resto de la app (en vez de depender de
    que la URL de MEDIA_URL sea o no adivinable)."""

    CAMPOS = {"logo": "logo", "sello": "sello_judicial", "firma": "firma"}

    def get(self, request, pk, campo):
        perfil = request.user.perfil
        juzgado = get_object_or_404(Juzgado, pk=pk)
        if not perfil.tiene_acceso_a(juzgado):
            raise PermissionDenied("No tienes acceso a este juzgado.")

        nombre_campo = self.CAMPOS.get(campo)
        if nombre_campo is None:
            raise Http404("Imagen no reconocida.")
        archivo = getattr(juzgado, nombre_campo)
        if not archivo:
            raise Http404("Este juzgado todavía no tiene esa imagen cargada.")
        return FileResponse(open(archivo.path, "rb"))


# ══════════════════════════════════════════════════════════════════════
# UTM vigente (Módulo 4 — ver motor/utm.py para la fuente y por qué)
# ══════════════════════════════════════════════════════════════════════

class ActualizarUTMView(PuedeEditarPlantillaMixin, View):
    """Un solo clic consulta el valor UTM vigente y lo deja guardado si
    cambió — nunca en segundo plano ni al cargar la página: afecta el
    monto de cada multa en la carta, así que lo dispara a propósito
    quien tiene permiso para editar la configuración del Juzgado."""

    def post(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "No hay ningún juzgado configurado todavía.")
            return redirect("procesos:panel")

        try:
            info = consultar_utm_vigente()
        except ErrorConsultaUTM as exc:
            logger.warning("Consulta UTM fallida: %s", exc)
            messages.error(
                request,
                "No se pudo consultar el valor UTM vigente (mindicador.cl no respondió). "
                "Inténtalo de nuevo en unos minutos; mientras tanto se sigue usando el valor guardado.",
            )
        else:
            anterior = juzgado.utm_valor
            periodo = f"{MESES[info['fecha'].month - 1]} {info['fecha'].year}"
            if info["valor"] == anterior:
                messages.success(
                    request,
                    f"El valor UTM ya estaba al día: {fmt_pesos(anterior)} "
                    f"(vigente {periodo}, fuente: {info['fuente']}).",
                )
            else:
                juzgado.utm_valor = info["valor"]
                juzgado.save(update_fields=["utm_valor"])
                messages.success(
                    request,
                    f"UTM actualizada de {fmt_pesos(anterior)} a {fmt_pesos(info['valor'])} "
                    f"(vigente {periodo}, fuente: {info['fuente']}).",
                )
        return redirect(f"{reverse('procesos:plantilla')}?juzgado={juzgado.slug}")

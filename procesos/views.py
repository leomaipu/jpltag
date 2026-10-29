"""
Vistas web: ingresar, panel, subir nómina, ver resultado, descargar
archivo, agenda de audiencias y plantilla del juzgado.

La generación de cartas en sí (lo que en los scripts originales era
`procesar()`) vive en procesos/generacion.py: aquí solo se recibe el
formulario y se muestra el resultado.
"""

import logging
import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from motor.cartas import fmt_pesos
from motor.fechas_en_letras import MESES
from motor.plantilla import ruta_plantilla_defecto
from motor.utm import ErrorConsultaUTM, consultar_utm_vigente
from nucleo.models import BloqueAjuste, BloqueHorario, Juzgado, TipoCarta, ahora_local

from . import generacion
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

def _juzgados_visibles(perfil):
    return perfil.juzgados_visibles()


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
    def _pantalla(self, request, perfil, juzgado, form, *, repetido=None):
        return render(request, "procesos/subir_nomina.html", {
            "form": form, "juzgado": juzgado, "juzgados": _juzgados_visibles(perfil),
            "repetido": repetido,
        })

    def get(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "No tienes ningún juzgado asignado o no hay ninguno configurado todavía.")
            return redirect("procesos:panel")
        return self._pantalla(request, perfil, juzgado, SubirNominaForm())

    def post(self, request):
        perfil = request.user.perfil
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "No tienes ningún juzgado asignado o no hay ninguno configurado todavía.")
            return redirect("procesos:panel")

        form = SubirNominaForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._pantalla(request, perfil, juzgado, form)

        datos = form.cleaned_data
        solicitud = generacion.SolicitudGeneracion(
            tipo_carta=datos["tipo_carta"],
            fecha_sentencia=datos.get("fecha_audiencia"),
            nombre_firmante=datos["nombre_firmante"],
            cargo_firma=datos["cargo_firma"],
            n_cartas_paquete=datos.get("n_cartas_paquete") or 0,
            repetidas=datos.get("repetidas") or "",
        )
        if datos.get("nomina"):
            solicitud.archivo_nomina = datos["nomina"]
            solicitud.nombre_nomina = datos["nomina"].name
            solicitud.archivo_tipo_causas = datos.get("tipo_causas")
        else:
            guardada = generacion.leer_nomina_guardada(datos["nomina_guardada"], juzgado, request.user)
            if guardada is None:
                form.add_error("nomina", "La nómina guardada ya no está disponible: vuelve a elegir el Excel.")
                return self._pantalla(request, perfil, juzgado, form)
            solicitud.nomina_guardada, solicitud.nombre_nomina = guardada

        try:
            corrida = generacion.preparar(juzgado, request.user, solicitud)
        except generacion.EnvioRepetido as exc:
            form = SubirNominaForm(initial={**datos, "nomina_guardada": exc.token, "repetidas": None})
            return self._pantalla(request, perfil, juzgado, form, repetido={
                "mensaje": str(exc), "nombre_nomina": solicitud.nombre_nomina,
                "faltan": exc.n_total - exc.n_repetidas,
            })
        except generacion.GeneracionRechazada as exc:
            form.add_error(None, str(exc))
            return self._pantalla(request, perfil, juzgado, form)
        return redirect("procesos:resultado", pk=corrida.pk)


# ══════════════════════════════════════════════════════════════════════
# Resultado y descarga
# ══════════════════════════════════════════════════════════════════════

def _archivo_del_usuario(request, pk):
    archivo = get_object_or_404(ArchivoGenerado.objects.select_related("generacion__juzgado"), pk=pk)
    if not request.user.perfil.tiene_acceso_a(archivo.generacion.juzgado):
        raise PermissionDenied("No tienes acceso a este juzgado.")
    ruta_abs = Path(settings.SALIDAS_ROOT) / archivo.ruta
    if not ruta_abs.is_file():
        raise Http404("El archivo ya no está disponible.")
    return archivo, ruta_abs


class ResultadoView(PuedeGenerarCartasMixin, View):
    # Mientras está en cola o en curso, la pantalla se recarga sola.
    RECARGAR_CADA = 10  # s

    def get(self, request, pk):
        perfil = request.user.perfil
        corrida = get_object_or_404(GeneracionCartas.objects.select_related("juzgado", "usuario"), pk=pk)
        if not perfil.tiene_acceso_a(corrida.juzgado):
            raise PermissionDenied("No tienes acceso a este juzgado.")
        activa = corrida.estado in generacion.ESTADOS_ACTIVOS
        return render(request, "procesos/resultado.html", {
            "generacion": corrida,
            "archivos": corrida.archivos.all(),
            "mes_nombre": MESES[corrida.mes_periodo - 1] if corrida.mes_periodo else None,
            "recargar_cada": self.RECARGAR_CADA if activa else None,
            "antes_en_la_cola": generacion.corridas_antes_en_la_cola(corrida),
            "interrumpida": generacion.corrida_interrumpida(corrida),
        })


class DescargarArchivoView(PuedeGenerarCartasMixin, View):
    def get(self, request, pk):
        archivo, ruta_abs = _archivo_del_usuario(request, pk)
        return FileResponse(open(ruta_abs, "rb"), as_attachment=True, filename=archivo.nombre_descarga)


class DescargarZipView(PuedeGenerarCartasMixin, View):
    """Un paquete de cartas comprimido en .zip: se arma al pedirlo, en el
    directorio temporal (TMPDIR), y no queda guardado."""

    def get(self, request, pk):
        import tempfile
        import zipfile

        archivo, ruta_abs = _archivo_del_usuario(request, pk)
        comprimido = tempfile.TemporaryFile()
        with zipfile.ZipFile(comprimido, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(ruta_abs, arcname=archivo.nombre_descarga)
        comprimido.seek(0)
        return FileResponse(
            comprimido, as_attachment=True, filename=f"{Path(archivo.nombre_descarga).stem}.zip",
            content_type="application/zip",
        )


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
        c.interrumpida = generacion.corrida_interrumpida(c)
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
        if corrida is None or not generacion.corrida_interrumpida(corrida):
            messages.error(request, "Esa generación no se puede devolver: ya terminó o sigue en marcha.")
            return redirect("procesos:bloque_editar", pk=bloque.pk)
        tomados = {int(k): v for k, v in corrida.cupos_por_bloque.items()}
        generacion.devolver_cupos(tomados)
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

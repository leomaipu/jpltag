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

from collections import Counter
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
from nucleo.models import BloqueHorario, Juzgado, TipoCarta

from .forms import BloqueHorarioForm, PlantillaJuzgadoForm, SubirNominaForm
from .models import ArchivoGenerado, GeneracionCartas
from .permisos import (
    PerfilRequeridoMixin,
    PuedeEditarAgendaMixin,
    PuedeEditarPlantillaMixin,
    PuedeGenerarCartasMixin,
)


# ══════════════════════════════════════════════════════════════════════
# Helpers de juzgado activo (un Administrador sin juzgado fijo ve los 3 y
# elige con cuál trabajar vía ?juzgado=<slug>)
# ══════════════════════════════════════════════════════════════════════

def _juzgados_visibles(perfil):
    if perfil.juzgado_id:
        return Juzgado.objects.filter(pk=perfil.juzgado_id)
    return Juzgado.objects.filter(activo=True)


def _juzgado_actual(request, perfil):
    visibles = _juzgados_visibles(perfil)
    slug = request.GET.get("juzgado") or request.POST.get("juzgado")
    if slug:
        return get_object_or_404(visibles, slug=slug)
    return visibles.first()


# ══════════════════════════════════════════════════════════════════════
# Ingreso / panel
# ══════════════════════════════════════════════════════════════════════

class IngresarView(LoginView):
    template_name = "procesos/login.html"
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
            BloqueHorario.objects.filter(juzgado__in=juzgados, fecha__gte=date.today())
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


def _pool_de_agenda(juzgado):
    """Arma la lista de slots disponibles (fecha dd/mm/aaaa, hora) a partir
    de los BloqueHorario futuros de `juzgado`, repitiendo cada slot tantas
    veces como cupos_disponibles tenga su bloque (un bloque es una
    audiencia grupal: varias patentes pueden compartir la misma fecha y
    hora). Devuelve (pool_de_slots, bloque_por_slot) — lo segundo permite
    luego descontar los cupos realmente usados en esta corrida."""
    bloques = list(
        juzgado.bloques_horario.filter(fecha__gte=date.today()).order_by("fecha", "hora")
    )
    bloque_por_slot = {}
    pool = []
    for b in bloques:
        slot = (b.fecha.strftime("%d/%m/%Y"), b.hora.strftime("%H:%M"))
        bloque_por_slot[slot] = b
        pool.extend([slot] * b.cupos_disponibles)
    return pool, bloque_por_slot


def _descontar_cupos_usados(slots, bloque_por_slot):
    """Después de asignar_agenda(), incrementa cupos_usados de cada
    BloqueHorario según cuántos de sus slots se asignaron realmente en
    esta corrida (para que la próxima corrida no vuelva a ofrecerlos)."""
    asignados = [slot for slot in slots.values() if slot != SLOT_SIN_AGENDA]
    consumo = Counter(asignados)
    for slot, cantidad in consumo.items():
        bloque = bloque_por_slot.get(slot)
        if bloque is not None:
            bloque.cupos_usados = min(bloque.cupos_usados + cantidad, bloque.cupos_totales)
            bloque.save(update_fields=["cupos_usados"])


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
            pool_agenda, bloque_por_slot = _pool_de_agenda(juzgado)
            slots, sin_cupo = asignar_agenda(incluidos, pool_agenda)
            _descontar_cupos_usados(slots, bloque_por_slot)

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

        generacion = GeneracionCartas.objects.create(
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
            estado=estado,
        )

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
        return _registrar_fallo(juzgado, usuario, archivo_nomina.name, carpeta_rel, str(exc))


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
        mes_nombre = MESES[generacion.mes_periodo - 1].capitalize() if generacion.mes_periodo else None
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
class AgendaView(PerfilRequeridoMixin, View):
    def get(self, request):
        perfil = request.user.perfil
        juzgados = _juzgados_visibles(perfil)
        juzgado = _juzgado_actual(request, perfil)
        bloques = (
            BloqueHorario.objects.filter(juzgado=juzgado).order_by("fecha", "hora")
            if juzgado else BloqueHorario.objects.none()
        )
        form = BloqueHorarioForm() if perfil.puede_editar_agenda() else None
        return render(request, "procesos/agenda.html", {
            "perfil": perfil, "juzgados": juzgados, "juzgado": juzgado,
            "bloques": bloques, "form": form,
        })

    def post(self, request):
        perfil = request.user.perfil
        if not perfil.puede_editar_agenda():
            raise PermissionDenied("Tu rol no puede editar la agenda.")
        juzgado = _juzgado_actual(request, perfil)
        if juzgado is None:
            messages.error(request, "Elige un juzgado antes de agregar un bloque.")
            return redirect("procesos:agenda")

        form = BloqueHorarioForm(request.POST)
        if form.is_valid():
            bloque = form.save(commit=False)
            bloque.juzgado = juzgado
            try:
                bloque.save()
                messages.success(request, "Bloque de horario agregado.")
            except IntegrityError:
                messages.error(request, "Ya existe un bloque para esa fecha y hora en este juzgado.")
            return redirect(f"{reverse('procesos:agenda')}?juzgado={juzgado.slug}")

        bloques = BloqueHorario.objects.filter(juzgado=juzgado).order_by("fecha", "hora")
        return render(request, "procesos/agenda.html", {
            "perfil": perfil, "juzgados": _juzgados_visibles(perfil), "juzgado": juzgado,
            "bloques": bloques, "form": form,
        })


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
            messages.error(request, f"No se pudo consultar el valor UTM vigente: {exc}")
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

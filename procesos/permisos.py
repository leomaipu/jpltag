"""
Mixins de control de acceso basados en nucleo.models.PerfilUsuario — la
matriz de permisos por rol (Operador/Secretario(a)/Jueza-Juez/
Administrador) confirmada con el usuario el 10-oct-2026.

Cada vista de procesos hereda de uno de estos mixins en vez de repetir la
misma lógica de "¿tiene perfil? ¿su rol puede hacer esto?" en cada view.
El control de acceso POR JUZGADO (que un Secretario de JPL1 no pueda ver
los resultados de JPL2) se revisa aparte, en la propia vista, con
perfil.tiene_acceso_a(juzgado) — porque depende de qué objeto se está
pidiendo, no solo del rol.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class PerfilRequeridoMixin(LoginRequiredMixin):
    """Exige que el usuario, además de estar autenticado, tenga un
    PerfilUsuario asignado. Sin perfil no hay rol ni juzgado — no hay
    nada que esa cuenta pueda hacer en procesos todavía."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not hasattr(request.user, "perfil"):
            raise PermissionDenied(
                "Tu usuario todavía no tiene un perfil asignado. Pide a un "
                "Administrador que te asigne un rol y un juzgado."
            )
        return super().dispatch(request, *args, **kwargs)


def _mixin_por_permiso(nombre_metodo, mensaje):
    """Fábrica de mixins: cada uno exige que
    `request.user.perfil.<nombre_metodo>()` sea verdadero antes de dejar
    pasar a la vista."""

    class _Mixin(PerfilRequeridoMixin):
        def dispatch(self, request, *args, **kwargs):
            if request.user.is_authenticated and hasattr(request.user, "perfil"):
                permiso = getattr(request.user.perfil, nombre_metodo)
                if not permiso():
                    raise PermissionDenied(mensaje)
            return super().dispatch(request, *args, **kwargs)

    _Mixin.__name__ = f"Mixin_{nombre_metodo}"
    return _Mixin


PuedeGenerarCartasMixin = _mixin_por_permiso(
    "puede_generar_cartas",
    "Tu rol no tiene permiso para generar ni consultar cartas (solo "
    "Operador, Secretario(a) y Administrador pueden).",
)

# TODO(permisos-agenda): este mixin no se usa todavía — AgendaView (en
# views.py) revisa perfil.puede_editar_agenda() a mano dentro de su
# post(), en vez de heredar de PuedeEditarAgendaMixin. Hoy da lo mismo:
# los 4 roles tienen "agenda: editar" en True en PERMISOS_POR_ROL (ver
# nucleo/models.py), así que no hay ningún caso real que lo distinga. Si
# algún día se restringe la agenda por rol (ej. que Jueza/Juez deje de
# poder editarla), conviene que AgendaView.post() pase a heredar de este
# mixin en vez de repetir la comprobación inline — así el cambio en
# PERMISOS_POR_ROL se refleja automáticamente ahí también. Confirmado con
# el usuario el 11-oct-2026: dejarlo anotado, no resolverlo todavía.
PuedeEditarAgendaMixin = _mixin_por_permiso(
    "puede_editar_agenda",
    "Tu rol no tiene permiso para editar la agenda.",
)

PuedeGestionarUsuariosMixin = _mixin_por_permiso(
    "puede_gestionar_usuarios",
    "Tu rol no tiene permiso para gestionar usuarios (solo Administrador).",
)

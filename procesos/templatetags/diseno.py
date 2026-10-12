"""Ayudas de plantilla para el diseño (sin lógica de negocio)."""

from django import template

register = template.Library()


@register.inclusion_tag("procesos/_cupos.html")
def cupos(bloque):
    """Medidor de cupos de un BloqueHorario: turquesa mientras sobra
    lugar, ámbar cuando queda poco y magenta cuando está lleno."""
    total = max(bloque.cupos_totales, 1)
    return {
        "usados": bloque.cupos_usados,
        "total": bloque.cupos_totales,
        "max": total,
        "low": int(total * 0.7),
        "high": int(total * 0.95),
    }

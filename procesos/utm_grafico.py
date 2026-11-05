"""Geometría de la «escalera» de la UTM. Se dibuja en el servidor porque la CSP
no admite estilos en línea y la pantalla debe funcionar sin JavaScript.

La UTM rige un mes completo: por eso cada mes es un tramo plano y el cambio un
escalón, sin líneas oblicuas que sugieran valores intermedios. Los meses que la
fuente no entregue quedan como un corte, no como un tramo inventado. El dibujo se
estira al ancho disponible (los trazos no se deforman: ver app.css)."""

from motor.cartas import fmt_pesos
from motor.fechas_en_letras import MESES

from .utm import periodo

ANCHO = 720
ALTO = 120
MARGEN_Y = 12
MINIMO_DE_PUNTOS = 3


def _coord(numero):
    """Texto, no float: la plantilla escribiría «348,0» por la localización y
    el navegador no lo aceptaría como coordenada."""
    return f"{numero:.1f}"


def _indice(fecha):
    return fecha.year * 12 + fecha.month - 1


def _variacion(actual, anterior):
    porcentaje = (actual - anterior) * 100 / anterior
    signo = "+" if porcentaje > 0 else "−" if porcentaje < 0 else ""
    return f"{signo}{abs(porcentaje):.1f}".replace(".", ",") + " %"


def construir(serie):
    """Datos listos para la plantilla, o None si hay muy pocos puntos."""
    if len(serie) < MINIMO_DE_PUNTOS:
        return None
    valores = [v.valor for v in serie]
    bajo, alto = min(valores), max(valores)
    rango = (alto - bajo) or 1
    inicio = _indice(serie[0].vigente_desde)
    meses = _indice(serie[-1].vigente_desde) - inicio + 1
    paso = ANCHO / meses
    alto_util = ALTO - 2 * MARGEN_Y

    puntos, trazo = [], []
    indice_previo = None
    for item in serie:
        indice = _indice(item.vigente_desde) - inicio
        x = indice * paso
        y = MARGEN_Y + alto_util * (alto - item.valor) / rango
        orden = "L" if indice_previo == indice - 1 else "M"
        trazo.append(f"{orden}{_coord(x)} {_coord(y)} H{_coord(x + paso)}")
        indice_previo = indice
        puntos.append({
            "x": _coord(x),
            "centro": _coord(x + paso / 2),
            "y": _coord(y),
            "periodo": periodo(item.vigente_desde),
            "pesos": fmt_pesos(item.valor),
        })

    ultimo, previo = serie[-1], serie[-2]
    return {
        "ancho": ANCHO,
        "alto": ALTO,
        "paso": _coord(paso),
        "trazo": " ".join(trazo),
        "puntos": puntos,
        "ultimo": {**puntos[-1], "x_fin": _coord(ANCHO)},
        "iniciales": [
            MESES[(inicio + i) % 12][:3] for i in range(meses)
        ],
        "desde": periodo(serie[0].vigente_desde),
        "hasta": periodo(ultimo.vigente_desde),
        "bajo": fmt_pesos(bajo),
        "alto_pesos": fmt_pesos(alto),
        "variacion": _variacion(ultimo.valor, previo.valor),
        "mes_previo": MESES[previo.vigente_desde.month - 1],
    }

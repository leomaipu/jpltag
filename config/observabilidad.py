"""Filtros de lo que se envía a GlitchTip."""


def sin_migas_http(miga, _pista):
    """Descarta el registro de peticiones HTTP salientes: incluye la query."""
    return None if miga.get("category") == "httplib" else miga

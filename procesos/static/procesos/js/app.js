/*
  jpltag — mejoras progresivas. Todo funciona sin este archivo (formularios
  normales, menús <details>); acá solo se agrega comodidad. Vive en un
  archivo propio porque la CSP del servidor no admite scripts dentro del
  HTML (script-src 'self').
*/
(function () {
  "use strict";
  var doc = document;
  doc.documentElement.classList.add("js");

  function tamano(bytes) {
    if (bytes < 1024 * 1024) { return Math.max(1, Math.round(bytes / 1024)) + " KB"; }
    return (bytes / (1024 * 1024)).toFixed(1).replace(".", ",") + " MB";
  }

  function mostrarArchivo(input) {
    var zona = input.closest(".soltar");
    if (!zona) { return; }
    var nombre = zona.querySelector(".soltar__nombre");
    var titulo = zona.querySelector(".soltar__titulo");
    if (input.files && input.files.length) {
      var f = input.files[0];
      nombre.textContent = f.name + " (" + tamano(f.size) + ")";
      nombre.hidden = false;
      if (titulo) { titulo.textContent = "Archivo listo. Toca para cambiarlo"; }
    } else {
      nombre.hidden = true;
      if (titulo) { titulo.textContent = titulo.getAttribute("data-inicial"); }
    }
  }

  // Si el formulario vuelve con errores, el foco va al resumen para que se lea.
  var errores = doc.querySelector("[data-enfocar]");
  if (errores) { errores.focus({ preventScroll: false }); }

  // El selector de juzgado envía el formulario al cambiar.
  doc.addEventListener("change", function (e) {
    var t = e.target;
    if (t.matches("[data-autoenviar]") && t.form) { t.form.submit(); }
    if (t.matches("input[type=file]")) { mostrarArchivo(t); }
  });

  // Arrastrar y soltar sobre la zona del Excel.
  ["dragenter", "dragover"].forEach(function (nombre) {
    doc.addEventListener(nombre, function (e) {
      var zona = e.target.closest && e.target.closest(".soltar");
      if (zona) { e.preventDefault(); zona.classList.add("arrastrando"); }
    });
  });
  ["dragleave", "drop"].forEach(function (nombre) {
    doc.addEventListener(nombre, function (e) {
      var zona = e.target.closest && e.target.closest(".soltar");
      if (zona) { zona.classList.remove("arrastrando"); }
    });
  });

  // La fecha de sentencia se muestra solo con «Sentencia». El CSS lo hace con
  // :has(); esta clase cubre los navegadores que no lo tienen (Firefox < 121).
  function marcarSentencia(form) {
    var elegido = form.querySelector('.tarjetas-tipo input:checked');
    form.classList.toggle("es-sentencia", !!elegido && elegido.value === "SENTENCIA");
  }
  doc.querySelectorAll("form[data-procesa]").forEach(marcarSentencia);
  doc.addEventListener("change", function (e) {
    var form = e.target.closest("form[data-procesa]");
    if (form && e.target.closest(".tarjetas-tipo")) { marcarSentencia(form); }
  });

  // Acciones que no se deshacen (cancelar una generación): piden confirmación.
  doc.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.matches("[data-confirmar]") && !window.confirm(form.getAttribute("data-confirmar"))) {
      e.preventDefault();
    }
  });

  // Revisar la nómina toma unos segundos: se muestra el estado de espera en
  // vez de dejar una pantalla quieta.
  doc.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches("[data-procesa]") || !form.checkValidity()) { return; }
    window.setTimeout(function () {
      // Un segundo clic enviaría la misma nómina dos veces.
      form.querySelectorAll('button[type="submit"]').forEach(function (b) { b.disabled = true; });
      form.classList.add("enviando");
      var aviso = form.querySelector(".procesando");
      if (aviso) { aviso.focus(); window.scrollTo({ top: 0 }); }
    }, 0);
  });

  // Mantenedor de bloques: botones − / + y vista en vivo de los cupos.
  (function () {
    var vista = doc.querySelector("[data-vista-cupos]");
    var total = doc.getElementById("id_cupos_totales");
    if (!vista || !total) { return; }
    var usadosCampo = doc.getElementById("id_cupos_usados");
    var medidor = vista.querySelector("meter");
    var tUsados = vista.querySelector("[data-vista-usados]");
    var tTotal = vista.querySelector("[data-vista-total]");
    var original = parseInt(vista.getAttribute("data-usados"), 10) || 0;

    function entero(campo, defecto) {
      var n = parseInt(campo && campo.value, 10);
      return isNaN(n) ? defecto : n;
    }
    function pintar() {
      var t = Math.max(entero(total, 1), 1);
      var u = usadosCampo && usadosCampo.value !== "" ? entero(usadosCampo, original) : original;
      if (medidor) {
        medidor.max = t;
        medidor.low = Math.floor(t * 0.7);
        medidor.high = Math.floor(t * 0.95);
        medidor.value = Math.min(u, t);
      }
      if (tUsados) { tUsados.textContent = u; }
      if (tTotal) { tTotal.textContent = t; }
    }
    doc.querySelectorAll("[data-paso]").forEach(function (b) {
      b.hidden = false;
      b.addEventListener("click", function () {
        var minimo = parseInt(total.min, 10) || 1;
        var maximo = parseInt(total.max, 10) || 500;
        var nuevo = entero(total, minimo) + parseInt(b.getAttribute("data-paso"), 10);
        total.value = Math.min(Math.max(nuevo, minimo), maximo);
        pintar();
      });
    });
    total.addEventListener("input", pintar);
    if (usadosCampo) { usadosCampo.addEventListener("input", pintar); }
  })();

  // Serie de horarios: cuántos bloques saldrían, antes de crearlos.
  (function () {
    var form = doc.querySelector("[data-serie]");
    if (!form) { return; }
    var salida = form.querySelector("[data-serie-resumen]");
    var anuncio = form.querySelector("[data-serie-anuncio]");
    var MAXIMO = 48;
    function minutos(texto) {
      var m = /^(\d{1,2}):(\d{2})/.exec(texto || "");
      return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : null;
    }
    function hhmm(n) {
      return ("0" + Math.floor(n / 60)).slice(-2) + ":" + ("0" + (n % 60)).slice(-2);
    }
    function resumir() {
      var d = minutos(form.elements.desde.value);
      var h = minutos(form.elements.hasta.value);
      var c = parseInt(form.elements.cada.value, 10);
      var texto = "", problema = false;
      if (d === null || h === null || isNaN(c) || c < 5) {
        texto = "";
      } else if (h < d) {
        texto = "La última audiencia no puede ser antes que la primera.";
        problema = true;
      } else {
        var n = Math.floor((h - d) / c) + 1;
        if (n > MAXIMO) {
          texto = "Así saldrían " + n + " bloques; el máximo por vez es " + MAXIMO + ". Sube el intervalo o acorta el rango.";
          problema = true;
        } else {
          var horas = [];
          for (var m = d; m <= h; m += c) { horas.push(hhmm(m)); }
          var lista = horas.length > 4
            ? horas.slice(0, 3).join(", ") + " … " + horas[horas.length - 1]
            : horas.join(", ");
          texto = horas.length + (horas.length === 1 ? " bloque: " : " bloques: ") + lista + ".";
        }
      }
      salida.textContent = texto;
      salida.classList.toggle("serie__resumen--error", problema);
      return texto;
    }
    form.addEventListener("input", resumir);
    // Al lector de pantalla se le avisa al terminar de editar un campo, no en cada tecla.
    form.addEventListener("change", function () { if (anuncio) { anuncio.textContent = resumir() || ""; } });
    resumir();
  })();

  // Tras guardar un bloque la página salta a su fila: el aviso queda a la
  // vista un momento aunque el bloque esté más abajo, y se retira solo.
  (function () {
    var avisos = doc.querySelector(".mensajes");
    var ancla = window.location.hash ? window.location.hash.slice(1) : "";
    // getElementById y no :target: al cargar el script, el navegador aún no lo resolvió.
    if (!avisos || !ancla || !doc.getElementById(decodeURIComponent(ancla))) { return; }
    if (avisos.querySelector(".error, .warning")) { return; }   // los errores no se retiran
    avisos.classList.add("mensajes--ancla");
    window.setTimeout(function () {
      avisos.classList.add("mensajes--saliendo");
      window.setTimeout(function () { avisos.classList.add("mensajes--retirado"); }, 450);
    }, 6000);
  })();

  // UTM: el gráfico se lee con el dedo o el mouse. Sin JavaScript queda el valor
  // de cada mes en su título y en la tabla.
  doc.querySelectorAll("[data-utm-serie]").forEach(function (figura) {
    var svg = figura.querySelector("svg");
    var zonas = figura.querySelectorAll(".utm-escalera__zona");
    var lectura = figura.querySelector("[data-utm-lectura]");
    var resumen = figura.querySelector("[data-utm-resumen]");
    var punto = figura.querySelector("[data-utm-punto]");
    var guia = figura.querySelector("[data-utm-guia]");
    var inicial = punto.getAttribute("d");
    var espera = null;

    function volver() {
      punto.setAttribute("d", inicial);
      guia.setAttribute("hidden", "");
      lectura.hidden = true;
      resumen.hidden = false;
    }

    // Se busca el mes más cercano al puntero: con el dedo, el navegador deja de
    // avisar qué elemento hay debajo una vez que empieza el arrastre.
    function seguir(e) {
      window.clearTimeout(espera);
      var caja = svg.getBoundingClientRect();
      var x = (e.clientX - caja.left) * svg.viewBox.baseVal.width / caja.width;
      var cerca = zonas[0];
      zonas.forEach(function (z) {
        if (Math.abs(z.getAttribute("data-x") - x) < Math.abs(cerca.getAttribute("data-x") - x)) { cerca = z; }
      });
      var centro = cerca.getAttribute("data-x");
      punto.setAttribute("d", "M" + centro + " " + cerca.getAttribute("data-y") + "h0.01");
      guia.setAttribute("x1", centro);
      guia.setAttribute("x2", centro);
      guia.removeAttribute("hidden");
      lectura.textContent = cerca.getAttribute("data-texto");
      lectura.hidden = false;
      resumen.hidden = true;
    }

    svg.addEventListener("pointerdown", seguir);
    svg.addEventListener("pointermove", seguir);
    // Con el mouse vuelve al salir; con el dedo, un momento después de soltar.
    svg.addEventListener("pointerleave", function (e) {
      if (e.pointerType === "mouse") { volver(); return; }
      espera = window.setTimeout(volver, 3000);
    });
  });

  // El botón dice dónde se va a guardar, y no se confirma sobre nada.
  doc.querySelectorAll("[data-utm-confirma]").forEach(function (form) {
    var boton = form.querySelector("[data-utm-boton]");
    var marcas = form.querySelectorAll('input[name="juzgados"]');
    var valor = form.getAttribute("data-pesos");
    function pintar() {
      var n = form.querySelectorAll('input[name="juzgados"]:checked').length;
      boton.disabled = n === 0;
      boton.textContent = n === 0 ? "Marca un juzgado"
        : marcas.length === 1 ? "Confirmar " + valor
        : "Confirmar " + valor + " en " + n + (n === 1 ? " juzgado" : " juzgados");
    }
    form.addEventListener("change", pintar);
    pintar();
  });

  // Si la persona vuelve con "Atrás", el navegador puede restaurar la página
  // con el estado de espera encendido: se apaga.
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) {
      doc.querySelectorAll("form.enviando").forEach(function (f) { f.classList.remove("enviando"); });
    }
  });

  // El menú de usuario se cierra al tocar fuera o con Escape.
  doc.addEventListener("click", function (e) {
    doc.querySelectorAll("details.menu[open]").forEach(function (m) {
      if (!m.contains(e.target)) { m.removeAttribute("open"); }
    });
  });
  doc.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") { return; }
    doc.querySelectorAll("details.menu[open]").forEach(function (m) {
      m.removeAttribute("open");
      var s = m.querySelector("summary");
      if (s) { s.focus(); }
    });
  });
})();

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

  // Generar cartas puede tardar minutos con una nómina grande: se muestra
  // el estado de espera en vez de dejar una pantalla quieta.
  doc.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches("[data-procesa]") || !form.checkValidity()) { return; }
    window.setTimeout(function () {
      form.classList.add("enviando");
      var aviso = form.querySelector(".procesando");
      if (aviso) { aviso.focus(); window.scrollTo({ top: 0 }); }
    }, 0);
  });

  // "Dejar de esperar": corta la espera sin recargar. La generación puede
  // haber seguido en el servidor, por eso el aviso pide revisar el Inicio.
  doc.addEventListener("click", function (e) {
    var b = e.target.closest("[data-cancelar]");
    if (!b) { return; }
    var form = b.closest("form");
    if (window.stop) { window.stop(); }
    if (form) { form.classList.remove("enviando"); }
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

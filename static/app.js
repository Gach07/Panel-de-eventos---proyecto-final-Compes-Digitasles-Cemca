(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // Datos del usuario que inició sesión, puestos por el servidor en la página.
  // Sirven para mostrar u ocultar botones; el servidor vuelve a revisar todo.
  const yo = JSON.parse($("yo").textContent);
  const puedeCrear = yo.puedeCrear;

  const detalle = $("detalle");
  const vacio = $("detalle-vacio");
  const form = $("form-evento");
  const campos = form.elements;

  let eventos = [];      // copia local de lo que hay guardado en el servidor
  let abiertoId = null;  // id del evento abierto en el panel (null = evento nuevo)
  let temporizadorAviso = null;
  let chatEventoId = null;
  const tiposVisibles = new Set(["evento", "reunion", "proyecto"]);

  // Una conexión WebSocket por pestaña. El servidor decide si la sesión es válida.
  const socket = io();

  // ---------------------------------------------------------------------
  // Comunicación con el servidor
  // ---------------------------------------------------------------------

  async function api(url, opciones) {
    const respuesta = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...opciones,
    });
    if (respuesta.status === 401) {
      window.location.href = "/login";
      throw new Error("Tu sesión terminó. Inicia sesión de nuevo.");
    }
    const datos = respuesta.status === 204 ? null : await respuesta.json();
    if (!respuesta.ok) {
      throw new Error((datos && datos.error) || "Algo salió mal. Inténtalo de nuevo.");
    }
    return datos;
  }

  async function cargarEventos() {
    eventos = await api("/api/eventos");
    calendario.refetchEvents();
  }

  // ---------------------------------------------------------------------
  // Chat en tiempo real
  // ---------------------------------------------------------------------

  function mostrarChat(mostrar) {
    $("chat").hidden = !mostrar;
    if (!mostrar) {
      $("chat-mensajes").innerHTML = "";
      $("chat-error").textContent = "";
      $("chat-input").value = "";
    }
  }

  function mostrarEstadoChat(texto, conectado) {
    const estado = $("chat-estado");
    estado.textContent = texto;
    estado.dataset.conectado = conectado ? "true" : "false";
  }

  function limpiarMensajesChat() {
    $("chat-mensajes").innerHTML = "";
  }

  function agregarMensajeChat(mensaje) {
    const contenedor = $("chat-mensajes");
    const articulo = document.createElement("article");
    articulo.className = "chat-mensaje" + (mensaje.usuario_id === yo.id ? " propio" : "");

    const cabecera = document.createElement("div");
    cabecera.className = "chat-mensaje-cabecera";

    const nombre = document.createElement("strong");
    nombre.textContent = mensaje.usuario;

    const fecha = document.createElement("time");
    const fechaReal = new Date(mensaje.fecha);
    fecha.textContent = Number.isNaN(fechaReal.getTime())
      ? mensaje.fecha
      : fechaReal.toLocaleString("es-CR", { dateStyle: "short", timeStyle: "short" });

    cabecera.append(nombre, fecha);

    const texto = document.createElement("p");
    texto.textContent = mensaje.mensaje;

    articulo.append(cabecera, texto);
    contenedor.appendChild(articulo);
    contenedor.scrollTop = contenedor.scrollHeight;
  }

  function unirseAlChat(eventId) {
    if (chatEventoId && chatEventoId !== eventId) {
      socket.emit("leave_event", { evento_id: chatEventoId });
    }

    chatEventoId = eventId;
    mostrarChat(true);
    limpiarMensajesChat();
    $("chat-error").textContent = "";

    if (socket.connected) {
      mostrarEstadoChat("En tiempo real", true);
      socket.emit("join_event", { evento_id: eventId });
    } else {
      mostrarEstadoChat("Sin conexión", false);
    }
  }

  function salirDelChat() {
    if (chatEventoId) {
      socket.emit("leave_event", { evento_id: chatEventoId });
    }
    chatEventoId = null;
    mostrarChat(false);
  }

  socket.on("connect", () => {
    mostrarEstadoChat("En tiempo real", true);
    if (chatEventoId) {
      socket.emit("join_event", { evento_id: chatEventoId });
    }
  });

  socket.on("disconnect", () => {
    mostrarEstadoChat("Sin conexión", false);
  });

  socket.on("connect_error", () => {
    mostrarEstadoChat("Sin conexión", false);
  });

  socket.on("chat_history", (datos) => {
    if (datos.evento_id !== chatEventoId) return;
    limpiarMensajesChat();
    datos.mensajes.forEach(agregarMensajeChat);
  });

  socket.on("new_message", (mensaje) => {
    if (mensaje.evento_id !== chatEventoId) return;
    agregarMensajeChat(mensaje);
  });

  socket.on("chat_error", (datos) => {
    $("chat-error").textContent = datos.error || "No se pudo realizar la operación.";
  });

  socket.on("event_deleted", (datos) => {
    if (datos.evento_id !== chatEventoId) return;
    if (abiertoId === datos.evento_id) {
      cerrarPanel();
      avisar("El evento fue eliminado.");
    } else {
      salirDelChat();
    }
    cargarEventos().catch(() => {});
  });

  function enviarMensajeChat() {
    const input = $("chat-input");
    const mensaje = input.value.trim();
    if (!mensaje || !chatEventoId) return;

    $("chat-error").textContent = "";
    socket.emit("send_message", {
      evento_id: chatEventoId,
      mensaje: mensaje,
    });
    input.value = "";
    input.focus();
  }

  $("btn-enviar-chat").addEventListener("click", enviarMensajeChat);

  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      enviarMensajeChat();
    }
  });

  // ---------------------------------------------------------------------
  // Calendario
  // ---------------------------------------------------------------------

  const calendario = new FullCalendar.Calendar($("calendario"), {
    locale: "es",
    initialView: "dayGridMonth",
    height: "100%",
    nowIndicator: true,
    dayMaxEvents: 3,
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: "dayGridMonth,timeGridWeek,listWeek",
    },
    events: (info, exito) => {
      exito(eventos.filter((e) => tiposVisibles.has(e.tipo)));
    },
    eventClassNames: (arg) => ["tipo-" + arg.event.extendedProps.tipo],
    dateClick: (info) => {
      if (!puedeCrear.length) return; // los colaboradores no crean eventos
      // En la vista semanal dateStr trae la hora; en la mensual solo la fecha.
      const [fecha, hora] = partirFechaHora(info.dateStr.slice(0, 16));
      abrirNuevo(fecha, hora);
    },
    eventClick: (info) => abrirEditar(info.event.id),
  });

  calendario.render();

  // ---------------------------------------------------------------------
  // Panel de detalle
  // ---------------------------------------------------------------------

  function partirFechaHora(iso) {
    if (!iso) return ["", ""];
    const [fecha, resto] = iso.split("T");
    return [fecha, resto ? resto.slice(0, 5) : ""];
  }

  function hoyISO() {
    const d = new Date();
    const mes = String(d.getMonth() + 1).padStart(2, "0");
    const dia = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${mes}-${dia}`;
  }

  function actualizarHoras() {
    const todoDia = campos.todo_dia.checked;
    $("fila-horas").hidden = todoDia;
    campos.hora_inicio.required = !todoDia;
  }

  function actualizarColorPanel() {
    detalle.dataset.tipo = campos.tipo.value;
  }

  // Al crear o editar solo se ofrecen los tipos que el rol puede usar.
  // En modo lectura solo se muestra el tipo del evento.
  function ajustarTipos(tipoActual, soloLectura) {
    document.querySelectorAll(".tipo-opcion").forEach((opcion) => {
      const valor = opcion.querySelector("input").value;
      opcion.hidden = soloLectura ? valor !== tipoActual : !puedeCrear.includes(valor);
    });
  }

  function bloquearCampos(bloquear) {
    for (const campo of campos) {
      if (campo.name) campo.disabled = bloquear;
    }
  }

  function mostrarError(texto) {
    $("error").textContent = texto || "";
  }

  function mostrarFormulario(datos, esNuevo, soloLectura) {
    abiertoId = esNuevo ? null : datos.id;

    $("detalle-titulo").textContent = esNuevo ? "Nuevo evento" : "Detalles del evento";
    $("btn-guardar").textContent = esNuevo ? "Crear evento" : "Guardar cambios";
    $("btn-guardar").hidden = soloLectura;
    $("btn-eliminar").hidden = esNuevo || soloLectura;

    $("detalle-creador").textContent = datos.creador ? `Creado por ${datos.creador}` : "";
    $("detalle-creador").hidden = !datos.creador;

    campos.titulo.value = datos.titulo;
    campos.tipo.value = datos.tipo;
    campos.fecha.value = datos.fecha;
    campos.todo_dia.checked = datos.todoDia;
    campos.hora_inicio.value = datos.horaInicio;
    campos.hora_fin.value = datos.horaFin;
    campos.descripcion.value = datos.descripcion;

    actualizarHoras();
    actualizarColorPanel();
    ajustarTipos(datos.tipo, soloLectura);
    bloquearCampos(soloLectura);
    mostrarError("");

    if (esNuevo) {
      salirDelChat();
    } else {
      unirseAlChat(datos.id);
    }

    vacio.hidden = true;
    form.hidden = false;
    if (!soloLectura) campos.titulo.focus();

    if (window.matchMedia("(max-width: 960px)").matches) {
      detalle.scrollIntoView({ behavior: "smooth" });
    }
  }

  function abrirNuevo(fecha, hora) {
    if (!puedeCrear.length) return;
    mostrarFormulario(
      {
        titulo: "",
        tipo: puedeCrear[0],
        fecha: fecha || hoyISO(),
        todoDia: !hora,
        horaInicio: hora || "",
        horaFin: "",
        descripcion: "",
        creador: "",
      },
      true,
      false
    );
  }

  function abrirEditar(id) {
    const evento = eventos.find((e) => e.id === id);
    if (!evento) return;

    const [fecha, horaInicio] = partirFechaHora(evento.start);
    const [, horaFin] = partirFechaHora(evento.end);

    mostrarFormulario(
      {
        id: evento.id,
        titulo: evento.title,
        tipo: evento.tipo,
        fecha: fecha,
        todoDia: evento.allDay,
        horaInicio: horaInicio,
        horaFin: horaFin,
        descripcion: evento.descripcion || "",
        creador: evento.creador,
      },
      false,
      !evento.puedeEditar
    );
  }

  function cerrarPanel() {
    salirDelChat();
    abiertoId = null;
    form.hidden = true;
    vacio.hidden = false;
    delete detalle.dataset.tipo;
  }

  function avisar(texto) {
    const zona = $("aviso");
    zona.textContent = texto;
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(() => { zona.textContent = ""; }, 3500);
  }

  // ---------------------------------------------------------------------
  // Eventos de la interfaz
  // ---------------------------------------------------------------------

  form.addEventListener("change", (e) => {
    if (e.target.name === "tipo") actualizarColorPanel();
    if (e.target.name === "todo_dia") actualizarHoras();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    mostrarError("");

    const fecha = campos.fecha.value;
    const todoDia = campos.todo_dia.checked;
    const horaInicio = campos.hora_inicio.value;
    const horaFin = campos.hora_fin.value;

    const datos = {
      title: campos.titulo.value.trim(),
      tipo: campos.tipo.value,
      descripcion: campos.descripcion.value.trim(),
      allDay: todoDia,
      start: todoDia ? fecha : `${fecha}T${horaInicio}`,
      end: todoDia || !horaFin ? null : `${fecha}T${horaFin}`,
    };

    const esNuevo = abiertoId === null;
    try {
      await api(esNuevo ? "/api/eventos" : `/api/eventos/${abiertoId}`, {
        method: esNuevo ? "POST" : "PUT",
        body: JSON.stringify(datos),
      });
      await cargarEventos();
      cerrarPanel();
      avisar(esNuevo ? "Evento creado." : "Cambios guardados.");
    } catch (err) {
      mostrarError(err.message);
    }
  });

  $("btn-eliminar").addEventListener("click", async () => {
    if (!abiertoId) return;
    if (!confirm("¿Eliminar este evento? Esta acción no se puede deshacer.")) return;

    try {
      await api(`/api/eventos/${abiertoId}`, { method: "DELETE" });
      await cargarEventos();
      cerrarPanel();
      avisar("Evento eliminado.");
    } catch (err) {
      mostrarError(err.message);
    }
  });

  $("btn-nuevo").addEventListener("click", () => abrirNuevo(hoyISO(), ""));
  $("btn-cerrar").addEventListener("click", cerrarPanel);

  document.querySelectorAll('input[name="filtro"]').forEach((casilla) => {
    casilla.addEventListener("change", () => {
      if (casilla.checked) tiposVisibles.add(casilla.value);
      else tiposVisibles.delete(casilla.value);
      calendario.refetchEvents();
    });
  });

  // ---------------------------------------------------------------------
  // Inicio
  // ---------------------------------------------------------------------

  if (!puedeCrear.length) {
    $("btn-nuevo").hidden = true;
    $("detalle-vacio-texto").textContent =
      "Selecciona un evento del calendario para ver sus detalles.";
  }

  cargarEventos().catch((err) => {
    avisar("No se pudieron cargar los eventos. " + err.message);
  });
})();

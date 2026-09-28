(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const ajustes = JSON.parse($("ajustes").textContent);
  const ROLES = ajustes.roles;
  const MI_ID = ajustes.yo;

  let usuarios = [];
  let temporizadorAviso = null;

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

  function avisar(texto) {
    const zona = $("aviso");
    zona.textContent = texto;
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(() => { zona.textContent = ""; }, 3500);
  }

  function opcionesDeRol(selector, valorActual, bloquearJefe) {
    selector.replaceChildren();
    for (const [valor, etiqueta] of Object.entries(ROLES)) {
      const opcion = document.createElement("option");
      opcion.value = valor;
      opcion.textContent = etiqueta + (bloquearJefe && valor === "jefe" ? " (fijo)" : "");
      opcion.disabled = bloquearJefe && valor !== "jefe";
      selector.appendChild(opcion);
    }
    selector.value = valorActual;
  }

  function pintarLista() {
    const cuerpo = $("lista-usuarios");
    cuerpo.replaceChildren();

    for (const usuario of usuarios) {
      const esMio = usuario.id === MI_ID;
      const fila = document.createElement("tr");

      const celdaNombre = document.createElement("td");
      celdaNombre.textContent = esMio ? `${usuario.nombre} (tú)` : usuario.nombre;

      const celdaUsuario = document.createElement("td");
      celdaUsuario.textContent = usuario.usuario;

      const celdaRol = document.createElement("td");
      celdaRol.textContent = ROLES[usuario.rol] + (esMio ? " 🔒" : "");

      const celdaAcciones = document.createElement("td");
      const editar = document.createElement("button");
      editar.type = "button";
      editar.className = "boton boton-chico";
      editar.textContent = "Editar";
      editar.addEventListener("click", () => abrirEdicion(usuario));
      celdaAcciones.appendChild(editar);

      if (!esMio) {
        const eliminar = document.createElement("button");
        eliminar.type = "button";
        eliminar.className = "boton boton-peligro boton-chico";
        eliminar.textContent = "Eliminar";
        eliminar.addEventListener("click", () => eliminarUsuario(usuario));
        celdaAcciones.appendChild(eliminar);
      }

      fila.append(celdaNombre, celdaUsuario, celdaRol, celdaAcciones);
      cuerpo.appendChild(fila);
    }
  }

  async function cargarUsuarios() {
    usuarios = await api("/api/usuarios");
    pintarLista();
  }

  function abrirEdicion(usuario) {
    const formulario = $("form-editar-usuario");
    const campos = formulario.elements;
    const esMio = usuario.id === MI_ID;

    formulario.dataset.id = usuario.id;
    campos.nombre.value = usuario.nombre;
    campos.usuario.value = usuario.usuario;
    campos.password.value = "";
    opcionesDeRol(campos.rol, usuario.rol, esMio);
    campos.rol.disabled = esMio;

    $("titulo-edicion").textContent = `Editar: ${usuario.nombre}`;
    $("nota-password").textContent = "Déjala vacía para conservar la contraseña actual.";
    $("error-edicion").textContent = "";
    $("edicion").hidden = false;
    campos.nombre.focus();
  }

  function cerrarEdicion() {
    $("edicion").hidden = true;
    $("form-editar-usuario").reset();
    $("error-edicion").textContent = "";
  }

  $("cancelar-edicion").addEventListener("click", cerrarEdicion);

  $("form-editar-usuario").addEventListener("submit", async (e) => {
    e.preventDefault();
    const formulario = e.currentTarget;
    const campos = formulario.elements;
    const id = formulario.dataset.id;
    $("error-edicion").textContent = "";

    try {
      const datos = {
        nombre: campos.nombre.value,
        usuario: campos.usuario.value,
        password: campos.password.value,
        rol: campos.rol.value,
      };

      const actualizado = await api(`/api/usuarios/${id}`, {
        method: "PUT",
        body: JSON.stringify(datos),
      });

      const indice = usuarios.findIndex((u) => u.id === id);
      if (indice !== -1) usuarios[indice] = actualizado;
      pintarLista();
      cerrarEdicion();
      avisar("Cuenta actualizada correctamente.");
    } catch (err) {
      $("error-edicion").textContent = err.message;
    }
  });

  async function eliminarUsuario(usuario) {
    if (!confirm(`¿Eliminar la cuenta de ${usuario.nombre}? Ya no podrá entrar.`)) return;
    try {
      await api(`/api/usuarios/${usuario.id}`, { method: "DELETE" });
      await cargarUsuarios();
      avisar("Cuenta eliminada.");
    } catch (err) {
      avisar(err.message);
    }
  }

  const formulario = $("form-usuario");

  formulario.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("error").textContent = "";

    const campos = formulario.elements;
    try {
      await api("/api/usuarios", {
        method: "POST",
        body: JSON.stringify({
          nombre: campos.nombre.value,
          usuario: campos.usuario.value,
          password: campos.password.value,
          rol: campos.rol.value,
        }),
      });
      formulario.reset();
      campos.rol.value = "colaborador";
      await cargarUsuarios();
      avisar("Usuario creado.");
    } catch (err) {
      $("error").textContent = err.message;
    }
  });

  opcionesDeRol($("nuevo-rol"), "colaborador", false);
  cargarUsuarios().catch((err) => avisar(err.message));
})();

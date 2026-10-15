(() => {
    "use strict";
    window.showDocToast = (message, kind = "info", options = {}) => {
        const icons = {success: "check-circle-fill", warning: "exclamation-circle-fill", error: "exclamation-triangle-fill", info: "info-circle-fill"};
        if (!icons[kind]) kind = "info";
        const zone = document.querySelector(".doc-toast-zone");
        if (!zone) return;
        const toast = document.createElement("div");
        toast.className = `toast doc-toast doc-toast-${kind}`;
        toast.setAttribute("role", kind === "error" ? "alert" : "status");
        toast.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
        toast.setAttribute("aria-atomic", "true");
        const marker = document.createElement("div");
        marker.className = "doc-toast-marker";
        const body = document.createElement("div");
        body.className = "doc-toast-body";
        const icon = document.createElement("div");
        icon.className = "doc-toast-icon";
        const glyph = document.createElement("i");
        glyph.className = `bi bi-${icons[kind]}`;
        icon.append(glyph);
        const text = document.createElement("div");
        text.className = "doc-toast-text";
        text.textContent = message;
        let input;
        if (options.copyValue) {
            input = document.createElement("input");
            input.className = "form-control form-control-sm mt-2";
            input.readOnly = true;
            input.value = options.copyValue;
            input.setAttribute("aria-label", "Ссылка на сканы ИД");
            text.append(input);
        }
        const close = document.createElement("button");
        close.type = "button";
        close.className = "btn-close doc-toast-close";
        close.setAttribute("aria-label", "Закрыть");
        close.addEventListener("click", () => toast.remove());
        body.append(icon, text, close);
        toast.append(marker, body);
        zone.append(toast);
        const persistent = kind === "error" || Boolean(input);
        const delay = kind === "warning" ? 8000 : 4200;
        if (window.bootstrap?.Toast) {
            const instance = window.bootstrap.Toast.getOrCreateInstance(toast, {autohide: !persistent, delay});
            toast.addEventListener("hidden.bs.toast", () => { instance.dispose(); toast.remove(); }, {once: true});
            close.addEventListener("click", () => instance.dispose(), {once: true});
            if (input) toast.addEventListener("shown.bs.toast", () => { input.focus(); input.select(); }, {once: true});
            instance.show();
        } else {
            toast.classList.add("show");
            if (!persistent) window.setTimeout(() => toast.remove(), delay);
            if (input) { input.focus(); input.select(); }
        }
    };
})();

"""Mispy dans la zone de notification : un point, et deux clics.

Le terminal convient pour régler l'outil une fois ; il ne convient pas pour
lancer un enregistrement entre deux réunions. Sur Mac, un petit programme Swift
tient ce rôle dans la barre de menus. Ici, le même rôle sous Windows.

Rien de la logique de Mispy ne vit ici : on lit le fichier d'état et on appelle
la commande `mispy`. Une seule implémentation à qui faire confiance, et c'est
celle qui est testée.
"""
from __future__ import annotations
import json, subprocess, sys, threading, time
from . import config, host, state

POLL_SECONDS = 2.0

# (libellé, couleur du point) par étape. Le gris est le repos, le rouge
# l'enregistrement, le violet le travail en cours, le vert le résultat.
LOOK = {
    state.IDLE:         ("Prêt",                      (120, 130, 140)),
    state.RECORDING:    ("Enregistre",                (220, 60, 60)),
    state.PAUSED:       ("En pause",                  (220, 160, 40)),
    state.DETAILS:      ("Attend tes précisions",     (150, 110, 200)),
    state.TRANSCRIBING: ("Transcrit",                 (150, 110, 200)),
    state.SYNTHESISING: ("Rédige le compte rendu",    (150, 110, 200)),
    state.BUILDING:     ("Met en page",               (150, 110, 200)),
    state.DONE:         ("Compte rendu prêt",         (60, 170, 90)),
    state.FAILED:       ("Arrêté sur une erreur",     (200, 80, 60)),
}


def view(job: dict) -> dict:
    """Tout ce que l'icône doit savoir, à partir du seul fichier d'état.

    Fonction pure : c'est elle qu'on teste, le dessin et le menu n'étant que de
    la plomberie par-dessus.
    """
    stage = job.get("stage", state.IDLE)
    label, colour = LOOK.get(stage, (stage, (120, 130, 140)))
    busy = stage in (state.DETAILS,) + state.RUNNING_STAGES
    title = (job.get("title") or "").strip()
    elapsed = ""
    if stage in (state.RECORDING, state.PAUSED):
        try:
            from . import capture
            mins = capture.minutes_captured(config.work_dir(), job.get("segments", []))
            elapsed = f"{int(mins):d}:{int(mins % 1 * 60):02d}"
        except Exception:
            elapsed = ""
    return {
        "stage": stage, "label": label, "colour": colour, "title": title,
        "elapsed": elapsed, "busy": busy,
        "can_start": stage in (state.IDLE, state.PAUSED, state.FAILED),
        "can_pause": stage == state.RECORDING,
        "can_stop": stage in (state.RECORDING, state.PAUSED),
        "can_discard": stage != state.IDLE and not busy,
        "can_open": bool(job.get("markdown") or job.get("pdf") or job.get("docx")),
        "tooltip": f"Mispy — {label}" + (f" · {title[:40]}" if title else "")
                   + (f" · {elapsed}" if elapsed else ""),
        "start_label": "Reprendre" if stage == state.PAUSED else "Démarrer",
    }


def icon_image(colour, size: int = 64):
    """Un disque plein de la couleur de l'état, dessiné plutôt qu'embarqué :
    pas de fichier à installer, et la couleur suit l'état."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 8
    d.ellipse([pad, pad, size - pad, size - pad], fill=tuple(colour) + (255,))
    return img


def _cli() -> list[str]:
    exe = config.BIN / ("mispy.cmd" if host.WINDOWS else "mispy")
    if exe.exists():
        return [str(exe)]
    return [sys.executable, "-c",
            f"import sys; sys.path.insert(0, {str(config.LIB)!r}); "
            "from mispy import cli; sys.exit(cli.main(sys.argv))", "mispy"]


def call(*args: str) -> str:
    try:
        r = subprocess.run(_cli() + list(args), capture_output=True, text=True,
                           timeout=300,
                           creationflags=0x08000000 if host.WINDOWS else 0)
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as exc:
        return f"mispy n'a pas répondu : {exc}"


def run() -> int:
    try:
        import pystray
    except Exception:
        print("Il manque le paquet `pystray` pour l'icône de notification.")
        print("  pip install pystray pillow      (ou relance l'installeur)")
        return 1
    from pystray import Menu, MenuItem

    current = {"view": view(state.load())}

    def v(key):
        return lambda *_: current["view"][key]

    def ask_name_and_start(icon, _item):
        # Pas de boîte de dialogue : le nom vient de l'agenda, et sinon de l'heure.
        # Nommer à la main se fait très bien depuis la ligne de commande.
        out = call("start")
        if out.startswith("✗"):
            icon.notify(out[:200], "Mispy")
        refresh_now()

    def do(*args):
        def handler(icon, _item):
            out = call(*args)
            if out.startswith("✗"):
                icon.notify(out[:200], "Mispy")
            refresh_now()
        return handler

    def quit_app(icon, _item):
        icon.visible = False
        icon.stop()

    menu = Menu(
        MenuItem(v("tooltip"), None, enabled=False),
        Menu.SEPARATOR,
        MenuItem(v("start_label"), ask_name_and_start, visible=v("can_start"), default=True),
        MenuItem("Mettre en pause", do("pause"), visible=v("can_pause")),
        MenuItem("Terminer et rédiger", do("stop"), visible=v("can_stop")),
        MenuItem("Supprimer la prise", do("discard"), visible=v("can_discard")),
        Menu.SEPARATOR,
        MenuItem("Ouvrir le dernier compte rendu", do("last"), visible=v("can_open")),
        MenuItem("Ouvrir le dossier des réunions",
                 lambda *_: host.open_path(config.load()["output"]["root"])),
        Menu.SEPARATOR,
        MenuItem("Quitter", quit_app),
    )

    icon = pystray.Icon("mispy", icon_image(current["view"]["colour"]),
                        current["view"]["tooltip"], menu)

    def refresh_now():
        new = view(state.load())
        if new != current["view"]:
            if new["colour"] != current["view"]["colour"]:
                icon.icon = icon_image(new["colour"])
            icon.title = new["tooltip"]
            current["view"] = new
            icon.update_menu()

    def loop():
        while True:
            time.sleep(POLL_SECONDS)
            try:
                refresh_now()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()
    icon.run()
    return 0

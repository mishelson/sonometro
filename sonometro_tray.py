import threading
from queue import Empty, Queue

import pystray
from PIL import Image, ImageDraw


PAUSED_COLOR = "#7d8794"


def icon_image(color: str, paused: bool) -> Image.Image:
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=14, fill="#131c28", outline=color, width=8)
    if paused:
        draw.rectangle((22, 20, 28, 44), fill=color)
        draw.rectangle((36, 20, 42, 44), fill=color)
    else:
        draw.ellipse((24, 24, 40, 40), fill=color)
    return image


class Tray:
    """Notification-area icon. Menu clicks arrive on pystray's thread and are queued for Tk."""

    def __init__(self) -> None:
        self.commands: Queue[str] = Queue()
        self.paused = True
        self.can_toggle = False
        self.appearance: tuple[str, bool] | None = None
        self.title = "Sonometro"
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar ventana", lambda icon, item: self.commands.put("show"), default=True),
            pystray.MenuItem(lambda item: "Reanudar" if self.paused else "Pausar (libera el microfono)",
                             lambda icon, item: self.commands.put("toggle"),
                             enabled=lambda item: self.can_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", lambda icon, item: self.commands.put("quit")),
        )
        self.icon: pystray.Icon | None = None
        ready = threading.Event()
        failure: list[BaseException] = []

        # The Win32 backend binds the icon window to the thread that creates it, so build and run it there.
        def run() -> None:
            try:
                self.icon = pystray.Icon("sonometro", icon_image(PAUSED_COLOR, True), self.title, menu)
            except BaseException as exc:
                failure.append(exc)
                ready.set()
                return
            self.icon.run(setup=lambda icon: (setattr(icon, "visible", True), ready.set()))

        self.thread = threading.Thread(target=run, name="sonometro-tray", daemon=True)
        self.thread.start()
        if not ready.wait(5):
            raise RuntimeError("El icono de la bandeja no respondio.")
        if failure:
            raise failure[0]

    def next_command(self) -> str | None:
        try:
            return self.commands.get_nowait()
        except Empty:
            return None

    def update(self, color: str, paused: bool, can_toggle: bool, title: str) -> None:
        menu_changed = paused != self.paused or can_toggle != self.can_toggle
        self.paused = paused
        self.can_toggle = can_toggle
        if (color, paused) != self.appearance:
            self.appearance = (color, paused)
            self.icon.icon = icon_image(color, paused)
        if title != self.title:
            self.title = title
            self.icon.title = title[:127]
        if menu_changed:
            self.icon.update_menu()

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, "Sonometro")
        except (NotImplementedError, OSError):
            pass

    def stop(self) -> None:
        self.icon.stop()
        self.thread.join(2)

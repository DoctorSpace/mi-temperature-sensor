"""Double-click launcher: no console window on Windows."""

import ctypes
from pathlib import Path
import traceback


if __name__ == "__main__":
    try:
        from widget import main
        main()
    except Exception:
        details = traceback.format_exc()
        folder = Path(__file__).resolve().parent
        ctypes.windll.user32.MessageBoxW(
            None,
            "Не удалось запустить виджет.\n\n"
            "Запустите install.cmd в папке проекта,\n"
            "затем start_widget.cmd.\n\n"
            f"Папка: {folder}\n\n{details}",
            "Датчик Xiaomi — ошибка запуска",
            0x10,
        )

"""Vendímetro: programa de escritorio con base de datos SQLite.

Abre su propia ventana; por dentro, un pequeño servidor local entrega la
pantalla y guarda los datos en Documentos\\Vendímetro\\ventas.db.
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

PUERTO = 8765
COLECCIONES = {"config", "productos", "clientes", "proveedores", "dias", "compras", "gastos", "ajustes", "abonos"}
RESPALDOS_A_GUARDAR = 30

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
WEB = BASE / "web" / "index.html"
CARPETA = Path(os.environ.get("VENDIMETRO_DATOS") or Path.home() / "Documents" / "Vendímetro")
DB_PATH = CARPETA / "ventas.db"
RESPALDOS = CARPETA / "Respaldos"
CARPETA_ANTERIOR = Path.home() / "Documents" / "Libro de Ventas"  # nombre del programa antes de llamarse Vendímetro

bloqueo = threading.Lock()


def migrar_carpeta_anterior():
    """Si hay datos de la versión «Libro de Ventas», los pasa a la carpeta nueva."""
    if os.environ.get("VENDIMETRO_DATOS") or CARPETA.exists() or not (CARPETA_ANTERIOR / "ventas.db").exists():
        return
    try:
        CARPETA_ANTERIOR.rename(CARPETA)
    except OSError:
        import shutil
        shutil.copytree(CARPETA_ANTERIOR, CARPETA)


def conectar():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS docs ("
        " col TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,"
        " actualizado TEXT NOT NULL, PRIMARY KEY (col, id))"
    )
    con.commit()
    return con


def respaldar(motivo="manual"):
    """Copia consistente de la base de datos a la carpeta Respaldos."""
    RESPALDOS.mkdir(parents=True, exist_ok=True)
    destino = RESPALDOS / f"ventas_{datetime.now():%Y-%m-%d_%H-%M-%S}_{motivo}.db"
    with bloqueo:
        dst = sqlite3.connect(destino)
        con.backup(dst)
        dst.close()
    copias = sorted(RESPALDOS.glob("ventas_*_auto.db"))
    for viejo in copias[:-RESPALDOS_A_GUARDAR]:
        viejo.unlink(missing_ok=True)
    return destino


def respaldo_diario():
    hoy = f"ventas_{datetime.now():%Y-%m-%d}_"
    if not any(p.name.startswith(hoy) for p in RESPALDOS.glob("ventas_*.db")):
        respaldar("auto")


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def responder(self, codigo, cuerpo=b"", tipo="application/json; charset=utf-8"):
        if isinstance(cuerpo, (dict, list)):
            cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def ruta_doc(self):
        partes = [unquote(p) for p in self.path.split("?")[0].strip("/").split("/")]
        if len(partes) == 4 and partes[:2] == ["api", "doc"] and partes[2] in COLECCIONES and partes[3]:
            return partes[2], partes[3]
        return None

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta in ("/", "/index.html"):
            return self.responder(200, WEB.read_bytes(), "text/html; charset=utf-8")
        if ruta.startswith("/fonts/"):
            archivo = WEB.parent / "fonts" / Path(unquote(ruta)).name
            tipos = {".woff2": "font/woff2", ".css": "text/css; charset=utf-8"}
            if archivo.suffix in tipos and archivo.is_file():
                return self.responder(200, archivo.read_bytes(), tipos[archivo.suffix])
        if ruta == "/api/all":
            with bloqueo:
                filas = con.execute("SELECT col, id, data FROM docs").fetchall()
            datos = {c: {} for c in COLECCIONES}
            for col, id_, data in filas:
                datos.setdefault(col, {})[id_] = json.loads(data)
            return self.responder(200, datos)
        if ruta == "/api/info":
            return self.responder(200, {"db": str(DB_PATH), "respaldos": str(RESPALDOS)})
        self.responder(404, {"error": "no encontrado"})

    def do_PUT(self):
        doc = self.ruta_doc()
        if not doc:
            return self.responder(404, {"error": "no encontrado"})
        try:
            largo = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(largo).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self.responder(400, {"error": "datos inválidos"})
        with bloqueo:
            con.execute(
                "INSERT INTO docs (col, id, data, actualizado) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(col, id) DO UPDATE SET data = excluded.data, actualizado = excluded.actualizado",
                (doc[0], doc[1], json.dumps(data, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )
            con.commit()
        self.responder(200, {"ok": True})

    def do_DELETE(self):
        doc = self.ruta_doc()
        if not doc:
            return self.responder(404, {"error": "no encontrado"})
        with bloqueo:
            con.execute("DELETE FROM docs WHERE col = ? AND id = ?", doc)
            con.commit()
        self.responder(200, {"ok": True})

    def do_POST(self):
        if self.path == "/api/respaldo":
            try:
                destino = respaldar("manual")
                return self.responder(200, {"ok": True, "archivo": str(destino)})
            except OSError as e:
                return self.responder(500, {"error": str(e)})
        if self.path == "/api/abrir-carpeta":
            if sys.platform == "win32":
                os.startfile(CARPETA)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(CARPETA)])
            return self.responder(200, {"ok": True})
        self.responder(404, {"error": "no encontrado"})


def normalizar(texto):
    """'Stock mínimo ' -> 'stockminimo', para reconocer columnas escritas a mano."""
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return "".join(c for c in texto.lower() if c.isalnum())


def dialogo(tipo, **kw):
    import webview
    clase = getattr(webview, "FileDialog", None)
    valor = getattr(clase, tipo, None) if clase else getattr(webview, f"{tipo}_DIALOG")
    ruta = webview.windows[0].create_file_dialog(valor, **kw)
    if not ruta:
        return None
    return ruta if isinstance(ruta, str) else ruta[0]


class ApiVentana:
    """Funciones que la pantalla puede llamar a través de window.pywebview.api."""

    def __init__(self):
        self._pendiente = ""
        self._forzar_cierre = False

    def set_pendiente(self, texto):
        """La pantalla avisa si hay una venta o compra a medias (texto vacío = nada)."""
        self._pendiente = texto or ""

    def cerrar(self):
        import webview
        self._forzar_cierre = True
        webview.windows[0].destroy()

    def al_cerrar(self, ventana):
        """Si hay trabajo a medias, cancela el cierre y pregunta en la pantalla."""
        if self._forzar_cierre or not self._pendiente:
            return True
        threading.Thread(target=lambda: ventana.evaluate_js("pedirCierre()"), daemon=True).start()
        return False

    def guardar_archivo(self, nombre, contenido):
        ruta = dialogo("SAVE", save_filename=nombre)
        if not ruta:
            return False
        Path(ruta).write_text(contenido, encoding="utf-8", newline="")
        return True

    def guardar_excel(self, nombre, hojas):
        """hojas: [{"titulo": str, "filas": [[encabezados...], [valores...], ...], "dinero": [índices]}]"""
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        ruta = dialogo("SAVE", save_filename=nombre, file_types=("Excel (*.xlsx)",))
        if not ruta:
            return False
        if not ruta.lower().endswith(".xlsx"):
            ruta += ".xlsx"
        libro = Workbook()
        libro.remove(libro.active)
        for hoja in hojas:
            ws = libro.create_sheet(str(hoja["titulo"])[:31])
            filas = hoja["filas"]
            for fila in filas:
                ws.append(fila)
            for celda in ws[1]:
                celda.font = Font(bold=True, color="FFFFFF")
                celda.fill = PatternFill("solid", fgColor="2F55D4")
            for i in hoja.get("dinero", []):
                for (celda,) in ws.iter_rows(min_row=2, min_col=i + 1, max_col=i + 1):
                    celda.number_format = "#,##0.00"
            for i in range(len(filas[0]) if filas else 0):
                largo = max((len(str(f[i])) for f in filas[:300] if i < len(f) and f[i] is not None), default=8)
                ws.column_dimensions[get_column_letter(i + 1)].width = min(max(largo + 2, 10), 60)
            ws.freeze_panes = "A2"
            if len(filas) > 1:
                ws.auto_filter.ref = ws.dimensions
        libro.save(ruta)
        return True

    def abrir_excel(self):
        """Lee la primera hoja de un Excel y devuelve sus filas como diccionarios."""
        from openpyxl import load_workbook

        ruta = dialogo("OPEN", file_types=("Excel (*.xlsx)",))
        if not ruta:
            return None
        try:
            ws = load_workbook(ruta, read_only=True, data_only=True).worksheets[0]
        except Exception:
            return {"error": "No se pudo leer el archivo. Guárdalo en Excel como «Libro de Excel (.xlsx)» e inténtalo de nuevo."}
        filas = ws.iter_rows(values_only=True)
        encabezados = [normalizar(c) for c in next(filas, [])]
        datos = []
        for n, fila in enumerate(filas, start=2):
            if not any(v not in (None, "") for v in fila):
                continue
            d = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(encabezados, fila) if k}
            d["_fila"] = n
            datos.append(d)
        return {"archivo": Path(ruta).name, "filas": datos}


def aviso(texto):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, texto, "Vendímetro", 0x40)
    elif sys.platform == "darwin":
        texto = texto.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display dialog "{texto}" with title "Vendímetro" buttons {{"OK"}}'])


if __name__ == "__main__":
    try:
        servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador)
    except OSError:
        aviso("Vendímetro ya está abierto. Búscalo en la barra de tareas (en Mac, en el Dock).")
        sys.exit(0)

    migrar_carpeta_anterior()
    CARPETA.mkdir(parents=True, exist_ok=True)
    con = conectar()
    respaldo_diario()
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PUERTO}/"

    if os.environ.get("VENDIMETRO_SIN_VENTANA"):
        threading.Event().wait()

    import webview
    api = ApiVentana()
    ventana = webview.create_window("Vendímetro", url, js_api=api, width=1360, height=860, min_size=(1000, 650), maximized=True)
    ventana.events.closing += lambda: api.al_cerrar(ventana)
    try:
        webview.start(private_mode=False, storage_path=str(CARPETA / ".ventana"), icon=str(BASE / "icono.ico"))
    finally:
        servidor.shutdown()
        con.close()

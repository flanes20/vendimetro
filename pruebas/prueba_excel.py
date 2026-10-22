import os, shutil, sys, tempfile, threading, time
from pathlib import Path
from openpyxl import Workbook, load_workbook

TMP = Path(tempfile.gettempdir()) / "libro_prueba_excel"
shutil.rmtree(TMP, ignore_errors=True); TMP.mkdir(parents=True)
os.environ["LIBRO_VENTAS_DATOS"] = str(TMP / "datos")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app, webview

# Excel escrito "a mano": títulos con mayúsculas/acentos, números como texto con coma decimal
entrada = TMP / "mis productos.xlsx"
wb = Workbook(); ws = wb.active
ws.append(["Producto", "Código", "Categoría", "Costo", "Precio de venta", "Stock", "Stock mínimo"])
ws.append(["Yerba 1 kg", 7791234, "Almacén", "1.900,50", 3100, 12, 4])
ws.append(["Fideos 500 g", "7790002", "Almacén", 800, "1.450", 30, None])
ws.append([None, None, None, None, None, None, None])
ws.append(["Sin precio", None, None, "abc", 100, 1, 1])
ws.append(["", "", "", 5, 5, 5, 5])
wb.save(entrada)

rutas = {"OPEN": str(entrada)}
def dialogo_falso(tipo, **kw):
    return rutas.get(tipo) or str(TMP / kw.get("save_filename", "salida.xlsx"))
app.dialogo = dialogo_falso

app.CARPETA.mkdir(parents=True, exist_ok=True)
app.con = app.conectar()
app.respaldo_diario()
srv = app.ThreadingHTTPServer(("127.0.0.1", 8797), app.Manejador)
threading.Thread(target=srv.serve_forever, daemon=True).start()
res = []
def ok(n, c, x=""): res.append(("OK " if c else "FALLA ") + n + (f"  ({x})" if x else ""))

def correr(w):
    time.sleep(4)
    w.evaluate_js("window.__err=[];addEventListener('error',e=>__err.push(e.message));addEventListener('unhandledrejection',e=>__err.push(String(e.reason)))")
    w.evaluate_js("ACT.go('productos'); ACT.importar('productos')"); time.sleep(2)
    ok("resumen de importación", "Importar productos" in (w.evaluate_js("$('#dlgT').textContent") or ""))
    plan = w.evaluate_js("JSON.stringify({n:importPendiente.plan.nuevos.length,c:importPendiente.plan.cambios.length,e:importPendiente.plan.errores})")
    ok("detecta 2 nuevos y 2 filas con problema", plan == '{"n":2,"c":0,"e":["Fila 5: «abc» no es un número válido en costo","Fila 6: falta el nombre"]}', plan)
    w.evaluate_js("ACT.importarOk()"); time.sleep(2)
    yerba = w.evaluate_js("JSON.stringify(Object.values(S.data.productos).find(p=>p.nombre==='Yerba 1 kg'))")
    ok("lee '1.900,50' como 1900.5 y el código numérico", '"costo":1900.5' in yerba and '"codigo":"7791234"' in yerba, yerba)
    # reimportar con stock distinto: debe actualizar, no duplicar
    wb2 = Workbook(); ws2 = wb2.active; ws2.append(["Nombre", "Codigo", "Stock"]); ws2.append(["Yerba 1 kg", "7791234", 7]); wb2.save(TMP / "conteo.xlsx")
    rutas["OPEN"] = str(TMP / "conteo.xlsx")
    w.evaluate_js("ACT.importar('productos')"); time.sleep(2); w.evaluate_js("ACT.importarOk()"); time.sleep(2)
    ok("reimportar actualiza sin duplicar", w.evaluate_js("Object.keys(S.data.productos).length") == 2)
    ok("stock actualizado a 7 con movimiento registrado", w.evaluate_js("Object.values(S.data.productos).find(p=>p.nombre==='Yerba 1 kg').stock") == 7 and w.evaluate_js("M().ajustes.some(a=>a.tipo==='conteo'&&a.delta===-5)"))
    # vender algo para que haya datos, y exportar
    pid = w.evaluate_js("Object.keys(S.data.productos)[0]")
    w.evaluate_js(f"ACT.go('pos'); ACT.posAdd('{pid}'); ACT.cobrar()"); time.sleep(2); w.evaluate_js("ACT.cerrar()")
    w.evaluate_js("ACT.go('ajustes'); ACT.excelTodo()"); time.sleep(2)
    w.evaluate_js("ACT.plantilla('clientes')"); time.sleep(1.5)
    w.evaluate_js("ACT.go('reportes'); ACT.csv('rep_producto')"); time.sleep(1.5)
    ok("sin errores en pantalla", not w.evaluate_js("__err"), w.evaluate_js("__err"))
    w.destroy()

w = webview.create_window("prueba excel", "http://127.0.0.1:8797/", js_api=app.ApiVentana())
webview.start(correr, w, private_mode=True)

todo = next(TMP.glob("Libro de Ventas - todo*.xlsx"), None)
ok("Excel completo creado", todo is not None)
if todo:
    lb = load_workbook(todo)
    ok("hojas del Excel completo", lb.sheetnames == ["Ventas", "Detalle de ventas", "Productos", "Clientes", "Compras", "Gastos"], lb.sheetnames)
    v = lb["Ventas"]
    ok("venta exportada con total numérico", v.max_row == 2 and isinstance(v["H2"].value, (int, float)), v["H2"].value)
ok("plantilla de clientes creada", (TMP / "Plantilla de clientes.xlsx").exists())
ok("reporte por producto creado", any(TMP.glob("Por producto*.xlsx")))
srv.shutdown()
print("\n".join(res))
sys.exit(1 if any(r.startswith("FALLA") for r in res) else 0)

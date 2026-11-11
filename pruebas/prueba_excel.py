import os, shutil, sys, tempfile, threading, time
from pathlib import Path
from openpyxl import Workbook, load_workbook

TMP = Path(tempfile.gettempdir()) / "vendimetro_prueba_excel"
shutil.rmtree(TMP, ignore_errors=True); TMP.mkdir(parents=True)
os.environ["VENDIMETRO_DATOS"] = str(TMP / "datos")
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
    w.evaluate_js("S.adminHasta=Date.now()+600000; ACT.go('productos'); ACT.importar('productos')"); time.sleep(2)
    ok("resumen de importación", "Importar productos" in (w.evaluate_js("$('#dlgT').textContent") or ""))
    plan = w.evaluate_js("JSON.stringify({n:importPendiente.plan.nuevos.length,c:importPendiente.plan.cambios.length,e:importPendiente.plan.errores})")
    ok("detecta 2 nuevos y 2 filas con problema", plan == '{"n":2,"c":0,"e":["Fila 5: «abc» no es un número válido en costo","Fila 6: falta el nombre"]}', plan)
    w.evaluate_js("ACT.importarOk()"); time.sleep(2)
    yerba = w.evaluate_js("JSON.stringify(Object.values(S.data.productos).find(p=>p.nombre==='Yerba 1 kg'))")
    ok("lee '1.900,50' como 1900.5 y el código numérico", '"costo":1900.5' in yerba and '"codigo":"7791234"' in yerba, yerba)
    # reimportar con stock distinto: debe actualizar, no duplicar
    wb2 = Workbook(); ws2 = wb2.active; ws2.append(["Nombre", "Codigo", "Stock"]); ws2.append(["Yerba 1 kg", "7791234", 7]); wb2.save(TMP / "conteo.xlsx")
    rutas["OPEN"] = str(TMP / "conteo.xlsx")
    w.evaluate_js("S.adminHasta=Date.now()+600000; ACT.importar('productos')"); time.sleep(2); w.evaluate_js("ACT.importarOk()"); time.sleep(2)
    ok("reimportar actualiza sin duplicar", w.evaluate_js("Object.keys(S.data.productos).length") == 2)
    ok("stock actualizado a 7 con movimiento registrado", w.evaluate_js("Object.values(S.data.productos).find(p=>p.nombre==='Yerba 1 kg').stock") == 7 and w.evaluate_js("M().ajustes.some(a=>a.tipo==='conteo'&&a.delta===-5)"))
    # clientes desde Excel: RUT sin formato se ordena, RUT inválido se rechaza, la dirección se guarda
    wb3 = Workbook(); ws3 = wb3.active
    ws3.append(["Nombre", "RUT", "Teléfono", "Dirección", "Comuna"])
    ws3.append(["María Gómez", "123456785", "+56 9 1111 2222", "Los Aromos 123", "Ñuñoa"])
    ws3.append(["RUT malo", "12.345.678-0", "", "", ""])
    ws3.append(["Ferretería El Clavo SpA", "76.123.456-0", "", "Av. Matta 456", "Santiago"])
    wb3.save(TMP / "clientes.xlsx"); rutas["OPEN"] = str(TMP / "clientes.xlsx")
    w.evaluate_js("S.adminHasta=Date.now()+600000; ACT.go('clientes'); ACT.importar('clientes')"); time.sleep(2)
    plan_c = w.evaluate_js("JSON.stringify({n:importPendiente.plan.nuevos.length,e:importPendiente.plan.errores})")
    ok("clientes: 2 nuevos y rechaza el RUT inválido", '"n":2' in plan_c and "Fila 3 (RUT malo)" in plan_c and "debería ser 5" in plan_c, plan_c)
    w.evaluate_js("ACT.importarOk()"); time.sleep(2)
    maria = w.evaluate_js("JSON.stringify(Object.values(S.data.clientes).find(c=>c.nombre==='María Gómez'))")
    ok("clientes: RUT con formato y dirección guardados", '"doc":"12.345.678-5"' in maria and '"comuna":"Ñuñoa"' in maria, maria)

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

todo = next(TMP.glob("Vendímetro - todo*.xlsx"), None)
ok("Excel completo creado", todo is not None)
if todo:
    lb = load_workbook(todo)
    ok("hojas del Excel completo", lb.sheetnames == ["Ventas", "Detalle de ventas", "Productos", "Clientes", "Compras", "Gastos"], lb.sheetnames)
    v = lb["Ventas"]
    ok("venta exportada con total numérico", v.max_row == 2 and isinstance(v["H2"].value, (int, float)), v["H2"].value)
    c = lb["Clientes"]
    ok("clientes exportados con RUT y dirección", [x.value for x in c[1]][:6] == ["Nombre", "RUT", "Teléfono", "Email", "Dirección", "Comuna"] and any(r[1].value == "12.345.678-5" for r in c.iter_rows(min_row=2)))
ok("plantilla de clientes creada", (TMP / "Plantilla de clientes.xlsx").exists())
ok("reporte por producto creado", any(TMP.glob("Por producto*.xlsx")))
srv.shutdown()
print("\n".join(res))
sys.exit(1 if any(r.startswith("FALLA") for r in res) else 0)

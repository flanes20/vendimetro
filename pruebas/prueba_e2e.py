import json, os, shutil, tempfile, sqlite3, sys, threading, time, urllib.request
from pathlib import Path

TMP = Path(tempfile.gettempdir()) / "libro_prueba_e2e"
shutil.rmtree(TMP, ignore_errors=True)
os.environ["LIBRO_VENTAS_DATOS"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import webview

app.CARPETA.mkdir(parents=True, exist_ok=True)
app.con = app.conectar()
app.respaldo_diario()
srv = app.ThreadingHTTPServer(("127.0.0.1", 8799), app.Manejador)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:8799/"
res = []

def ok(nombre, cond, extra=""):
    res.append(("OK " if cond else "FALLA ") + nombre + (f"  ({extra})" if extra else ""))

def js(w, code):
    return w.evaluate_js(code)

def esperar(w, segundos=1.2):
    time.sleep(segundos)

def correr(w):
    time.sleep(4)
    js(w, "window.__err=[];window.addEventListener('error',e=>window.__err.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__err.push('promesa: '+String(e.reason)));")
    ok("pantalla cargó y conectó a la base", js(w, "S.modo") == "db", js(w, "S.modo"))

    # ajustes del negocio
    js(w, "ACT.go('ajustes'); $('#nNombre').value='Almacén Don Pepe'; $('#nMoneda').value='$'; FORM.negocio();"); esperar(w)
    # producto nuevo con stock inicial 20
    js(w, "ACT.editProd(''); $('#fpNombre').value='Café 500 g'; $('#fpCodigo').value='7790001'; $('#fpCat').value='Almacén'; $('#fpCosto').value='100'; $('#fpPrecio').value='180'; $('#fpStock').value='20'; $('#fpMin').value='5'; FORM.prod(null,'');"); esperar(w)
    pid = js(w, "Object.keys(S.data.productos)[0]")
    ok("producto creado", bool(pid))
    # cliente
    js(w, "ACT.editCli(''); $('#fcNombre').value='María Gómez'; $('#fcTel').value='555-1234'; FORM.cli(null,'');"); esperar(w)
    cid = js(w, "Object.keys(S.data.clientes)[0]")
    ok("cliente creado", bool(cid))
    # venta 1: 3 unidades, efectivo, paga con 1000
    js(w, f"ACT.go('pos'); ACT.posAdd('{pid}'); ACT.posAdd('{pid}'); ACT.posAdd('{pid}'); S.pos.recibido='1000'; ACT.cobrar();"); esperar(w, 2)
    ok("venta en efectivo registrada (ticket mostrado)", js(w, "$('#dlgT').textContent") == "Venta registrada")
    js(w, "ACT.cerrar()")
    # venta 2: 2 unidades fiado a María con 10% descuento
    js(w, f"ACT.posAdd('{pid}'); ACT.posAdd('{pid}'); S.pos.cli='{cid}'; S.pos.pago='fiado'; S.pos.desc='10'; S.pos.descTipo='%'; ACT.cobrar();"); esperar(w, 2)
    js(w, "ACT.cerrar()")
    # compra de 10 a costo 90
    js(w, f"ACT.go('compras'); S.compra.items=[{{pid:'{pid}',cant:10,costo:90}}]; ACT.go('compras'); ACT.cmpGuardar();"); esperar(w, 2)
    # merma de 1
    js(w, f"ACT.ajuste('{pid}'); $('#ajTipo').value='merma'; $('#ajCant').value='1'; $('#ajNota').value='vencido'; FORM.ajuste(null,'{pid}');"); esperar(w)
    # gasto
    js(w, "ACT.go('gastos'); $('#gMonto').value='500'; $('#gDesc').value='Luz'; FORM.gasto();"); esperar(w)
    # pago parcial de María
    js(w, f"ACT.abono('{cid}'); $('#abMonto').value='100'; FORM.abono(null,'{cid}');"); esperar(w)
    # anular la venta 1
    key = js(w, "(()=>{const v=M().ventas.find(v=>v.folio===1);return v.fecha+'|'+v.id})()")
    js(w, f"ACT.anular('{key}'); ACT.confirmOk();"); esperar(w, 2)

    # venta a medias: se guarda sola, pregunta antes de cerrar y se recupera al reabrir
    js(w, f"ACT.go('pos'); ACT.posAdd('{pid}'); ACT.posAdd('{pid}'); S.pos.cli='{cid}';"); esperar(w)
    ok("avisa que hay una venta sin cobrar", api._pendiente.startswith("Tienes una venta sin cobrar"), api._pendiente)
    ok("no cierra si hay una venta a medias", api.al_cerrar(w) is False); esperar(w)
    ok("pregunta antes de cerrar", js(w, "$('#dlgT').textContent") == "¿Cerrar Libro de Ventas?")
    js(w, "ACT.cerrar(); ACT.go('panel')")
    # simular reapertura: vaciar la memoria y recuperar desde la base de datos, como hace el arranque
    guardado = json.loads(urllib.request.urlopen(URL + "api/all").read())["config"].get("borrador", {})
    ok("la venta a medias quedó guardada en la base", [i["cant"] for i in guardado.get("pos", {}).get("items", [])] == [2], json.dumps(guardado)[:200])
    js(w, f"S.pos.items=[]; S.pos.cli=''; S.page='panel'; recuperarBorrador({json.dumps(guardado)}); render();"); esperar(w)
    ok("al reabrir recupera la venta a medias", js(w, "S.pos.items.length===1 && S.pos.items[0].cant===2") and js(w, "S.pos.cli") == cid and js(w, "S.page") == "pos")
    js(w, "ACT.posVaciar()"); esperar(w)
    ok("al vaciar ya no avisa", api._pendiente == "" and api.al_cerrar(w) is True)

    # recorrer todas las pantallas y todos los modos de período
    js(w, "for (const p of Object.keys(PAGES)) { S.page=p; for (const m of ['dia','mes','anio','rango']) { P.modo=m; render(); } } P.modo='mes';")
    js(w, "for (const t of ['producto','categoria','cliente','pago']) { S.repTab=t; S.page='reportes'; render(); }")
    js(w, f"S.cliSel='{cid}'; ACT.go('clientes'); ACT.kardex('{pid}'); ACT.cerrar();")
    # demostración: entrar, recorrer, salir
    js(w, "ACT.verDemo(); for (const p of Object.keys(PAGES)) { S.page=p; render(); } ACT.salirDemo();"); esperar(w)
    ok("datos reales intactos después de la demostración", js(w, "S.data.productos[Object.keys(S.data.productos)[0]].nombre") == "Café 500 g")
    errs = js(w, "window.__err")
    ok("sin errores en pantalla", not errs, "; ".join(errs or []))
    w.destroy()

api = app.ApiVentana()
w = webview.create_window("prueba", URL, js_api=api, width=1300, height=850)
webview.start(correr, w, private_mode=True)

# leer la base de datos directamente desde el archivo, como si se reabriera el programa
srv.shutdown(); app.con.close()
db = sqlite3.connect(TMP / "ventas.db")
datos = {}
for col, id_, data in db.execute("SELECT col, id, data FROM docs"):
    datos.setdefault(col, {})[id_] = json.loads(data)
prod = list(datos["productos"].values())[0]
ventas = [v for d in datos.get("dias", {}).values() for v in d["ventas"]]
v1 = next(v for v in ventas if v["folio"] == 1); v2 = next(v for v in ventas if v["folio"] == 2)
ok("negocio guardado", datos["config"]["negocio"]["nombre"] == "Almacén Don Pepe")
ok("2 ventas guardadas", len(ventas) == 2)
ok("venta 1 anulada y conservada", v1["estado"] == "anulada")
ok("venta 1: total 540, recibido 1000", v1["total"] == 540 and v1["recibido"] == 1000)
ok("venta 2: fiado con 10% desc = 324", v2["pago"] == "fiado" and abs(v2["total"] - 324) < 0.01, v2["total"])
ok("stock final = 20 -3 -2 +10 -1 +3 (anulación) = 27", prod["stock"] == 27, prod["stock"])
ok("costo actualizado por la compra a 90", prod["costo"] == 90, prod["costo"])
ok("compra guardada", sum(len(d["lista"]) for d in datos.get("compras", {}).values()) == 1)
ok("gasto guardado", sum(len(d["lista"]) for d in datos.get("gastos", {}).values()) == 1)
ok("merma y stock inicial en el historial", sum(len(d["lista"]) for d in datos.get("ajustes", {}).values()) == 2)
ok("pago de cliente guardado", sum(len(d["lista"]) for d in datos.get("abonos", {}).values()) == 1)
ok("folio siguiente = 2", datos["config"]["negocio"]["folio"] == 2)
ok("copia de seguridad diaria creada", any((TMP / "Respaldos").glob("*_auto.db")))
print("\n".join(res))
sys.exit(1 if any(r.startswith("FALLA") for r in res) else 0)

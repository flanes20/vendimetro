import json, os, shutil, tempfile, sqlite3, sys, threading, time, urllib.error, urllib.request
from pathlib import Path

TMP = Path(tempfile.gettempdir()) / "vendimetro_prueba_e2e"
shutil.rmtree(TMP, ignore_errors=True)
os.environ["VENDIMETRO_DATOS"] = str(TMP)
os.environ["VENDIMETRO_SIN_NAVEGADOR"] = "1"
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
    # si una comprobación revienta, se anota como falla y la ventana se cierra igual (si no, la prueba queda colgada)
    try:
        pasos(w)
    except Exception as e:
        ok("la prueba terminó sin excepciones", False, repr(e)[:300])
    finally:
        try: w.destroy()
        except Exception: pass

def pasos(w):
    time.sleep(4)
    js(w, "window.__err=[];window.addEventListener('error',e=>window.__err.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__err.push('promesa: '+String(e.reason)));")
    ok("pantalla cargó y conectó a la base", js(w, "S.modo") == "db", js(w, "S.modo"))
    ok("muestra la versión instalada", js(w, "S.info.version") == app.VERSION)

    # ajustes del negocio
    js(w, "S.adminHasta=Date.now()+600000; ACT.go('ajustes'); $('#nNombre').value='Almacén Don Pepe'; $('#nMoneda').value='$'; FORM.negocio();"); esperar(w)
    # producto nuevo con stock inicial 20
    js(w, "ACT.editProd(''); $('#fpNombre').value='Café 500 g'; $('#fpCodigo').value='7790001'; $('#fpCat').value='Almacén'; $('#fpCosto').value='100'; $('#fpPrecio').value='180'; $('#fpStock').value='20'; $('#fpMin').value='5'; FORM.prod(null,'');"); esperar(w)
    pid = js(w, "Object.keys(S.data.productos)[0]")
    ok("producto creado", bool(pid))
    # cliente
    js(w, "ACT.editCli(''); $('#fcNombre').value='María Gómez'; $('#fcTel').value='555-1234'; FORM.cli(null,'');"); esperar(w)
    cid = js(w, "Object.keys(S.data.clientes)[0]")
    ok("cliente creado", bool(cid))
    # RUT: cálculo del dígito verificador, rechazo de inválidos y duplicados, formato y dirección
    ok("RUT: acepta válidos y rechaza inválidos", js(w, "[rut('12345678-5').ok, rut('11.111.111-1').ok, rut('76123456-0').ok, rut('12.345.678-K').ok, rut('1234').ok, rut('').ok]") == [True, True, True, False, False, True])
    ok("RUT: dígitos verificadores", js(w, "[rutDV('12345678'), rutDV('11111111'), rutDV('76123456'), rutDV('6')]") == ["5", "1", "0", "K"], js(w, "[rutDV('12345678'), rutDV('11111111'), rutDV('76123456'), rutDV('6')]"))
    js(w, "ACT.editCli(''); $('#fcNombre').value='Juan Pérez'; $('#fcDoc').value='12.345.678-9'; FORM.cli(null,'');"); esperar(w)
    ok("no guarda un RUT inválido", js(w, "Object.keys(S.data.clientes).length") == 1 and js(w, "$('#dlgT').textContent") == "Nuevo cliente")
    js(w, "$('#fcDoc').value='123456785'; $('#fcDir').value='Los Aromos 123'; $('#fcComuna').value='Ñuñoa'; FORM.cli(null,'');"); esperar(w)
    juan = js(w, "JSON.stringify(Object.values(S.data.clientes).find(c=>c.nombre==='Juan Pérez')||null)")
    ok("guarda RUT válido con formato y dirección", '"doc":"12.345.678-5"' in juan and '"dir":"Los Aromos 123"' in juan and '"comuna":"Ñuñoa"' in juan, juan)
    js(w, "ACT.editCli(''); $('#fcNombre').value='Otro'; $('#fcDoc').value='12345678-5'; FORM.cli(null,''); ACT.cerrar();"); esperar(w)
    ok("no permite dos clientes con el mismo RUT", js(w, "Object.values(S.data.clientes).filter(c=>c.doc==='12.345.678-5').length") == 1)
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
    js(w, f"S.adminHasta=Date.now()+600000; ACT.ajuste('{pid}'); $('#ajTipo').value='merma'; $('#ajCant').value='1'; $('#ajNota').value='vencido'; FORM.ajuste(null,'{pid}');"); esperar(w)
    # gasto
    js(w, "ACT.go('gastos'); $('#gMonto').value='500'; $('#gDesc').value='Luz'; FORM.gasto();"); esperar(w)
    # pago parcial de María
    js(w, f"ACT.abono('{cid}'); $('#abMonto').value='100'; FORM.abono(null,'{cid}');"); esperar(w)
    # anular la venta 1
    key = js(w, "(()=>{const v=M().ventas.find(v=>v.folio===1);return v.fecha+'|'+v.id})()")
    js(w, f"S.adminHasta=Date.now()+600000; ACT.anular('{key}'); ACT.confirmOk();"); esperar(w, 2)
    js(w, "S.adminHasta=0")  # hasta aquí se usó como administrador; el código se crea más abajo

    # venta a medias: se guarda sola, pregunta antes de cerrar y se recupera al reabrir
    js(w, f"ACT.go('pos'); ACT.posAdd('{pid}'); ACT.posAdd('{pid}'); S.pos.cli='{cid}';"); esperar(w)
    ok("avisa que hay una venta sin cobrar", api._pendiente.startswith("Tienes una venta sin cobrar"), api._pendiente)
    ok("no cierra si hay una venta a medias", api.al_cerrar(w) is False); esperar(w)
    ok("pregunta antes de cerrar", js(w, "$('#dlgT').textContent") == "¿Cerrar Vendímetro?")
    js(w, "ACT.cerrar(); ACT.go('panel')")
    # simular reapertura: vaciar la memoria y recuperar desde la base de datos, como hace el arranque
    guardado = json.loads(urllib.request.urlopen(URL + "api/all").read())["config"].get("borrador", {})
    ok("la venta a medias quedó guardada en la base", [i["cant"] for i in guardado.get("pos", {}).get("items", [])] == [2], json.dumps(guardado)[:200])
    js(w, f"S.pos.items=[]; S.pos.cli=''; S.page='panel'; recuperarBorrador({json.dumps(guardado)}); render();"); esperar(w)
    ok("al reabrir recupera la venta a medias", js(w, "S.pos.items.length===1 && S.pos.items[0].cant===2") and js(w, "S.pos.cli") == cid and js(w, "S.page") == "pos")
    js(w, "ACT.posVaciar()"); esperar(w)
    ok("al vaciar ya no avisa", api._pendiente == "" and api.al_cerrar(w) is True)

    # código de administrador y precio especial por cliente
    req = urllib.request.Request(URL + "api/doc/config/admin", data=b'{"hash":"x"}', method="PUT", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req); protegido = False
    except urllib.error.HTTPError as e:
        protegido = e.code == 403
    ok("el código no se puede pisar desde la pantalla", protegido)
    js(w, f"ACT.go('pos'); S.pos.cli='{cid}'; ACT.posAdd('{pid}'); ACT.posPrecio(0);"); esperar(w)
    ok("la primera vez pide crear el código", js(w, "$('#dlgT').textContent") == "Crear código de administrador")
    js(w, "$('#admNuevo').value='4321'; $('#admRepetir').value='4321'; FORM.adminCrear();"); esperar(w, 2)
    ok("después pide el nuevo precio", (js(w, "$('#dlgT').textContent") or "").startswith("Cambiar precio"))
    js(w, "$('#ppPrecio').value='150'; $('#ppEspecial').checked=true; FORM.posPrecio(null,'0');"); esperar(w)
    ok("precio cambiado en la venta", js(w, "S.pos.items[0].precio") == 150)
    ok("precio especial guardado en el cliente", js(w, f"S.data.clientes['{cid}'].precios['{pid}']") == 150)
    ok("el precio normal del producto no cambia", js(w, f"S.data.productos['{pid}'].precio") == 180)
    js(w, f"ACT.posVaciar(); S.pos.cli=''; ACT.posAdd('{pid}');"); esperar(w)
    ok("sin cliente usa el precio normal", js(w, "S.pos.items[0].precio") == 180)
    js(w, f"ACT.posCliPick('{cid}')"); esperar(w)
    ok("al elegir el cliente aplica su precio especial", js(w, "S.pos.items[0].precio") == 150)
    js(w, "ACT.posPrecio(0)"); esperar(w)
    ok("la vez siguiente pide el código", js(w, "$('#dlgT').textContent") == "Código de administrador")
    js(w, "$('#admPin').value='0000'; FORM.adminPin();"); esperar(w, 2)
    ok("rechaza un código incorrecto", js(w, "$('#dlgT').textContent") == "Código de administrador")
    js(w, "$('#admPin').value='4321'; FORM.adminPin();"); esperar(w, 2)
    ok("acepta el código correcto", (js(w, "$('#dlgT').textContent") or "").startswith("Cambiar precio"))
    js(w, "ACT.cerrar(); ACT.posVaciar();"); esperar(w)
    ok("el código no llega a la pantalla", "admin" not in json.loads(urllib.request.urlopen(URL + "api/all").read())["config"])

    # impresión: nota A4, ticket de 80 mm y apertura en el navegador (Mac)
    nota = js(w, "notaHTML(M().ventas.find(v=>v.folio===2))")
    ok("nota A4 con fecha, cliente y total, sin número ni nombre del programa", all(x in nota for x in ["FECHA", "María Gómez", "$324", "size:A4;margin:0", "Gracias por su compra"]) and not any(x in nota for x in ["Vendímetro", "Comprobante interno", "NOTA DE VENTA", "000002"]))
    ok("nota con N° 1, 2, 3 y productos en orden alfabético, sin columna código", (lambda h: "<th>Código</th>" not in h and '<td class="nro">1</td><td>Abeja</td>' in h and '<td class="nro">2</td><td>mango</td>' in h and '<td class="nro">3</td><td>Zapallo</td>' in h)(js(w, "notaHTML({fecha:hoy(),items:[{nombre:'Zapallo',cant:1,precio:1},{nombre:'mango',cant:1,precio:1},{nombre:'Abeja',cant:1,precio:1}],sub:3,total:3,pago:'efectivo'})")))
    ok("ticket con tamaño de 80 mm", "size:80mm" in js(w, "ticketDoc(M().ventas.find(v=>v.folio===2))"))
    ok("miles con punto y decimales con coma", js(w, "nf0.format(1234567) + ' ' + nf.format(2.5)") == "1.234.567 2,5", js(w, "nf0.format(1234567)"))
    r = json.loads(urllib.request.urlopen(urllib.request.Request(URL + "api/imprimir", data=json.dumps({"html": nota, "nombre": "nota-venta-2"}).encode(), method="POST", headers={"Content-Type": "application/json"})).read())
    ok("prepara la impresión para el navegador", r.get("ok") and Path(r["archivo"]).read_text(encoding="utf-8").startswith("<!doctype html>"))

    # producto cargado sin stock: se puede vender igual y el stock queda en negativo
    js(w, "ACT.editProd(''); $('#fpNombre').value='Pan amasado'; $('#fpPrecio').value='100'; FORM.prod(null,'');"); esperar(w)
    pan = js(w, "Object.keys(S.data.productos).find(k=>S.data.productos[k].nombre==='Pan amasado')")
    js(w, "ACT.go('pos')"); esperar(w, 0.5)
    ok("producto sin stock se puede tocar en el punto de venta", js(w, f"!document.querySelector('[data-act=posAdd][data-id=\"{pan}\"]').disabled"))
    js(w, f"ACT.posAdd('{pan}'); ACT.posAdd('{pan}'); ACT.cobrar();"); esperar(w, 2); js(w, "ACT.cerrar()")
    ok("venta sin stock registrada, stock queda en -2", js(w, f"S.data.productos['{pan}'].stock") == -2 and js(w, "M().ventas.some(v=>v.folio===3)"))

    # % de ventas por producto en la pantalla Ventas: las partes suman 100%
    js(w, "ACT.go('ventas')"); esperar(w, 0.5)
    filas = js(w, "JSON.stringify(CSV.pctProd.slice(1))")
    ok("% de ventas por producto suma 100%", "% de ventas por producto" in (js(w, "$('#content').textContent") or "") and abs(sum(f[3] for f in json.loads(filas)) - 100) < 0.5, filas)

    # eliminar un producto pide el código de administrador
    js(w, f"S.adminHasta=0; ACT.delProd('{pan}')"); esperar(w)
    ok("eliminar un producto pide el código", (js(w, "$('#dlgT').textContent") or "").startswith("Código de administrador"), js(w, "$('#dlgT').textContent"))
    js(w, "$('#admPin').value='4321'; FORM.adminPin();"); esperar(w, 2)
    ok("con el código pide confirmar la eliminación", js(w, "$('#dlgT').textContent") == "Eliminar producto", js(w, "$('#dlgT').textContent"))
    js(w, "S.adminHasta=0; ACT.cerrar();")

    # estado de pago: lo confirma el administrador desde el detalle de ventas
    v2id = js(w, "(v=>v.fecha+'|'+v.id)(M().ventas.find(v=>v.folio===2))")
    ok("venta nueva queda por confirmar", js(w, "estadoTxt(M().ventas.find(v=>v.folio===2))") == "Por confirmar")
    js(w, "S.adminHasta=0"); js(w, f"ACT.estadoPago('{v2id}')"); esperar(w)
    ok("cambiar el estado de pago pide el código", (js(w, "$('#dlgT').textContent") or "").startswith("Código de administrador"), js(w, "$('#dlgT').textContent"))
    js(w, "S.adminHasta=Date.now()+60000; ACT.cerrar();"); js(w, f"ACT.estadoPago('{v2id}')"); esperar(w)
    js(w, f"ACT.fijarPago('{v2id}|si')"); esperar(w)
    ok("administrador marca la venta como pagada", js(w, "M().ventas.find(v=>v.folio===2).pagada") is True and js(w, "estadoTxt(M().ventas.find(v=>v.folio===2))") == "Pagada")
    js(w, f"ACT.fijarPago('{v2id}|no')"); esperar(w)
    ok("y como no pagada", js(w, "M().ventas.find(v=>v.folio===2).pagada") is False)
    js(w, "S.adminHasta=0; ACT.cerrar();")

    # editar o borrar datos guardados pide el código; crear cosas nuevas no
    titulo = lambda: js(w, "$('#dlgT').textContent") or ""
    for accion, arg in [("anular", v2id), ("editProd", pid), ("ajuste", pid), ("delCompra", "x|y"), ("editProv", "x"), ("delProv", "x"), ("editCli", cid), ("delCli", cid), ("delGasto", "x|y"), ("importar", "productos"), ("restaurar", "")]:
        js(w, f"S.adminHasta=0; ACT.cerrar(); ACT.{accion}('{arg}')"); esperar(w)
        ok(f"sin código no deja: {accion}", titulo().startswith("Código de administrador"), titulo())
    js(w, "S.adminHasta=0; ACT.cerrar(); ACT.go('ajustes');"); esperar(w, 0.5); js(w, "FORM.negocio()"); esperar(w)
    ok("sin código no deja: datos del negocio", titulo().startswith("Código de administrador"), titulo())
    js(w, "S.adminHasta=0; ACT.cerrar(); ACT.editCli('')"); esperar(w)
    ok("crear un cliente nuevo no pide código", titulo() == "Nuevo cliente", titulo())
    js(w, "ACT.cerrar(); ACT.editProd('')"); esperar(w)
    ok("crear un producto nuevo no pide código", titulo() == "Nuevo producto", titulo())
    js(w, f"ACT.cerrar(); ACT.editCli('{cid}')"); esperar(w)
    js(w, "$('#admPin').value='4321'; FORM.adminPin();"); esperar(w, 2)
    ok("con el código se puede editar el cliente", titulo() == "Editar cliente", titulo())
    js(w, "S.adminHasta=0; ACT.cerrar();")

    # crear un cliente desde el punto de venta: queda guardado y elegido en la venta
    js(w, "ACT.go('pos'); const q=$('#posCliQ'); q.focus(); q.value='gomez'; q.dispatchEvent(new Event('input',{bubbles:true}));"); esperar(w, 0.5)
    ok("el buscador de clientes encuentra sin tildes ni mayúsculas", "María Gómez" in (js(w, "$('#posCliList').textContent") or "") and js(w, "!$('#posCliList').hidden"), js(w, "$('#posCliList').textContent"))
    js(w, "const q=$('#posCliQ'); q.value='12.345.678'; q.dispatchEvent(new Event('input',{bubbles:true}));"); esperar(w, 0.5)
    ok("y también por RUT", "Juan Pérez" in (js(w, "$('#posCliList').textContent") or ""))
    js(w, "const q=$('#posCliQ'); q.value='Rosa Díaz'; q.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#posCliList .nuevo').click();"); esperar(w)
    ok("crear cliente desde el buscador abre el formulario con el nombre escrito", js(w, "$('#dlgT').textContent") == "Nuevo cliente" and js(w, "$('#fcNombre').value") == "Rosa Díaz")
    js(w, "FORM.cli(null,'');"); esperar(w)
    nuevo = js(w, "Object.keys(S.data.clientes).find(k=>S.data.clientes[k].nombre==='Rosa Díaz')")
    ok("cliente creado desde la venta y elegido", bool(nuevo) and js(w, "S.pos.cli") == nuevo and js(w, "$('#posCliQ').value") == "Rosa Díaz")
    js(w, "S.pos.cli=''; render();")

    # los errores de pantalla se anotan en errores.txt
    js(w, "setTimeout(()=>{throw new Error('error de prueba')},0)"); esperar(w)
    ok("error anotado en errores.txt", "error de prueba" in (TMP / "errores.txt").read_text(encoding="utf-8") if (TMP / "errores.txt").exists() else False)
    js(w, "window.__err=window.__err.filter(e=>!String(e).includes('error de prueba'))")

    # recorrer todas las pantallas y todos los modos de período
    js(w, "for (const p of Object.keys(PAGES)) { S.page=p; for (const m of ['dia','mes','anio','rango']) { P.modo=m; render(); } } P.modo='mes';")
    js(w, "for (const t of ['producto','categoria','cliente','pago']) { S.repTab=t; S.page='reportes'; render(); }")
    js(w, f"S.cliSel='{cid}'; ACT.go('clientes'); ACT.kardex('{pid}'); ACT.cerrar();")
    # demostración: entrar, recorrer, salir
    js(w, "ACT.verDemo(); for (const p of Object.keys(PAGES)) { S.page=p; render(); } ACT.salirDemo();"); esperar(w)
    ok("datos reales intactos después de la demostración", js(w, "S.data.productos[Object.keys(S.data.productos)[0]].nombre") == "Café 500 g")
    errs = js(w, "window.__err")
    ok("sin errores en pantalla", not errs, "; ".join(errs or []))

api = app.ApiVentana()
w = webview.create_window("prueba", URL, js_api=api, width=1300, height=850)
webview.start(correr, w, private_mode=True)

# leer la base de datos directamente desde el archivo, como si se reabriera el programa
srv.shutdown(); app.con.close()
db = sqlite3.connect(TMP / "ventas.db")
datos = {}
for col, id_, data in db.execute("SELECT col, id, data FROM docs"):
    datos.setdefault(col, {})[id_] = json.loads(data)
prod = next(p for p in datos["productos"].values() if p["nombre"] == "Café 500 g")
ventas = [v for d in datos.get("dias", {}).values() for v in d["ventas"]]
v1 = next(v for v in ventas if v["folio"] == 1); v2 = next(v for v in ventas if v["folio"] == 2)
ok("negocio guardado", datos["config"]["negocio"]["nombre"] == "Almacén Don Pepe")
ok("3 ventas guardadas", len(ventas) == 3)
ok("venta 1 anulada y conservada", v1["estado"] == "anulada")
ok("venta 1: total 540, recibido 1000", v1["total"] == 540 and v1["recibido"] == 1000)
ok("venta 2: fiado con 10% desc = 324", v2["pago"] == "fiado" and abs(v2["total"] - 324) < 0.01, v2["total"])
ok("stock final = 20 -3 -2 +10 -1 +3 (anulación) = 27", prod["stock"] == 27, prod["stock"])
ok("costo actualizado por la compra a 90", prod["costo"] == 90, prod["costo"])
ok("compra guardada", sum(len(d["lista"]) for d in datos.get("compras", {}).values()) == 1)
ok("gasto guardado", sum(len(d["lista"]) for d in datos.get("gastos", {}).values()) == 1)
ok("merma y stock inicial en el historial", sum(len(d["lista"]) for d in datos.get("ajustes", {}).values()) == 2)
ok("pago de cliente guardado", sum(len(d["lista"]) for d in datos.get("abonos", {}).values()) == 1)
ok("último folio = 3", datos["config"]["negocio"]["folio"] == 3)
ok("copia de seguridad diaria creada", any((TMP / "Respaldos").glob("*_auto.db")))
admin = datos["config"].get("admin", {})
ok("código guardado cifrado, nunca a la vista", bool(admin.get("hash")) and bool(admin.get("sal")) and "4321" not in json.dumps(admin))
print("\n".join(res))
sys.exit(1 if any(r.startswith("FALLA") for r in res) else 0)

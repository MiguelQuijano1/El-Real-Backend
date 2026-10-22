"""Importación de maestros: valida con el mismo modelo del alta, no duplica por clave natural y es todo o nada."""
from app.schemas import masters as m
from app.services import master_import as svc
from app.services.masters import MasterConfig

SUPPLIERS = MasterConfig("suppliers", "suppliers", "Supplier", "Proveedor", "legal_name",
                         ("legal_name", "tax_id"), "PRV", import_key=("tax_id",))
CUSTOMERS = MasterConfig("customers", "customers", "Customer", "Cliente", "legal_name",
                         ("legal_name",), "CLI", import_key=("document_type", "document_number"))


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self._op, self._filters, self._data = db, name, "select", {}, None

    def select(self, *_a, **_k): self._op = "select"; return self
    def in_(self, col, vals): self._filters[col] = set(vals); return self
    def eq(self, col, val): self._filters[col] = {val}; return self
    def insert(self, data): self._op, self._data = "insert", data; return self
    def update(self, data): self._op, self._data = "update", data; return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self._op == "insert":
            new = {**self._data, "id": f"id-{len(rows) + 1}"}
            rows.append(new)
            return type("R", (), {"data": [new]})()
        match = [r for r in rows if all(r.get(k) in v for k, v in self._filters.items())]
        if self._op == "update":
            for r in match:
                r.update(self._data)
        return type("R", (), {"data": match})()


class _DB:
    def __init__(self, tables): self.tables = tables
    def table(self, name): return _Q(self, name)


class _Actor:
    actor = None
    id = "u1"


def _run(monkeypatch, tables, rows, dry_run, cfg=SUPPLIERS, model=m.SupplierIn, fields=None):
    fake = _DB(tables)
    audits = []
    monkeypatch.setattr(svc, "db", lambda: fake)
    monkeypatch.setattr(svc, "_next_code", lambda prefix: f"{prefix}-X")
    monkeypatch.setattr(svc, "record", lambda *a, **k: audits.append(k))
    res = svc.import_rows(cfg, model, rows, dry_run=dry_run, actor=_Actor(), meta=None, fields=fields)
    return res, fake, audits


def test_simulacion_no_escribe_y_marca_errores_por_fila(monkeypatch):
    rows = [{"legalName": "APM", "taxId": "20123456789"}, {"legalName": "", "taxId": "2010"}]
    res, fake, audits = _run(monkeypatch, {"suppliers": []}, rows, dry_run=True)
    assert [r["action"] for r in res["results"]] == ["create", "error"]
    assert res["results"][1]["row"] == 2 and res["results"][1]["errors"]
    assert "legal_name" in res["results"][1]["fields"]
    assert res["saved"] is False
    assert fake.tables["suppliers"] == [] and audits == []


def test_importar_crea_nuevos_y_actualiza_existentes(monkeypatch):
    tables = {"suppliers": [{"id": "old", "code": "PRV-00001", "tax_id": "20123456789", "legal_name": "Viejo"}]}
    rows = [{"legalName": "Nuevo nombre", "taxId": "20123456789"}, {"legalName": "Otro", "taxId": "20999999999"}]
    res, fake, audits = _run(monkeypatch, tables, rows, dry_run=False)
    assert [r["action"] for r in res["results"]] == ["update", "create"]
    assert fake.tables["suppliers"][0]["legal_name"] == "Nuevo nombre"
    assert fake.tables["suppliers"][0]["code"] == "PRV-00001"  # el código no cambia al actualizar
    assert fake.tables["suppliers"][1]["code"] == "PRV-X"
    assert res["summary"] == {"create": 1, "update": 1, "error": 0}
    assert res["saved"] is True and len(audits) == 1


def test_filas_repetidas_en_el_mismo_archivo_son_error(monkeypatch):
    rows = [{"legalName": "A", "taxId": "20111111111"}, {"legalName": "B", "taxId": "20111111111"}]
    res, _, _ = _run(monkeypatch, {"suppliers": []}, rows, dry_run=True)
    assert res["results"][1]["action"] == "error"
    assert "repetida" in res["results"][1]["errors"][0]
    assert "fila 2" in res["results"][1]["errors"][0]
    assert res["results"][1]["fields"] == ["tax_id"]  # fila de Excel del primer dato (la 1 son los encabezados)


def test_con_errores_no_se_guarda_nada(monkeypatch):
    rows = [{"legalName": "A", "taxId": "20111111111"}, {}]
    res, fake, _ = _run(monkeypatch, {"suppliers": []}, rows, dry_run=False)
    assert res["saved"] is False
    assert fake.tables["suppliers"] == []


def test_usa_las_reglas_del_alta_dni_de_8_digitos(monkeypatch):
    rows = [{"legalName": "Juan", "documentType": "DNI", "documentNumber": "1234567"}]
    res, _, _ = _run(monkeypatch, {"customers": []}, rows, dry_run=True, cfg=CUSTOMERS, model=m.CustomerIn)
    assert res["results"][0]["action"] == "error"
    assert "DNI debe tener 8" in res["results"][0]["errors"][0]
    assert res["results"][0]["fields"] == ["document_number"]  # para pintar solo esa celda


def test_clave_compuesta_no_confunde_dni_con_ruc(monkeypatch):
    tables = {"customers": [{"id": "c1", "document_type": "DNI", "document_number": "72513852", "legal_name": "Miguel"}]}
    rows = [{"legalName": "Miguel Q.", "documentType": "DNI", "documentNumber": "72513852"}]
    res, _, _ = _run(monkeypatch, tables, rows, dry_run=True, cfg=CUSTOMERS, model=m.CustomerIn)
    assert res["results"][0]["action"] == "update" and res["results"][0]["id"] == "c1"


def test_al_actualizar_solo_cambia_las_columnas_del_archivo(monkeypatch):
    tables = {"suppliers": [{"id": "old", "tax_id": "20123456789", "legal_name": "Viejo", "phone": "999111222", "payment_terms_days": 30}]}
    rows = [{"legalName": "Nuevo", "taxId": "20123456789"}]
    _, fake, _ = _run(monkeypatch, tables, rows, dry_run=False, fields=["legalName", "taxId"])
    row = fake.tables["suppliers"][0]
    assert row["legal_name"] == "Nuevo"
    assert row["phone"] == "999111222" and row["payment_terms_days"] == 30  # no se borran
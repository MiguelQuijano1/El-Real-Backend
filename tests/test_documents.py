"""Documentos con un Storage simulado en memoria (Supabase Storage real no se puede levantar en local)."""
import io
import uuid

import pytest

from tests.test_api_integration import ADMIN, auth, login  # noqa: F401


class FakeBucket:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def upload(self, path, file, file_options=None):
        assert path not in self.files
        self.files[path] = file

    def create_signed_url(self, path, expires_in, options=None):
        assert path in self.files
        return {"signedURL": f"https://storage.test/sign/{path}?token=abc&exp={expires_in}"}

    def remove(self, paths):
        for p in paths:
            self.files.pop(p, None)


class FakeStorage:
    def __init__(self):
        self.bucket = FakeBucket()

    def from_(self, _name):
        return self.bucket


@pytest.fixture()
def storage(api):
    from postgrest import SyncPostgrestClient
    import os

    from app.repositories import client as client_module

    real = SyncPostgrestClient(os.environ["TEST_POSTGREST_URL"])
    fake = FakeStorage()

    class Wrapper:
        storage = fake

        def table(self, name):
            return real.table(name)

        def rpc(self, fn, params=None, **kw):
            return real.rpc(fn, params, **kw)

    client_module.set_client(Wrapper())
    yield fake
    client_module.set_client(real)


def test_document_lifecycle(api, storage):
    token = login(api, **ADMIN)["accessToken"]
    h = auth(token)
    pdf = io.BytesIO(b"%PDF-1.4 contenido de prueba")
    r = api.post("/api/v1/documents", headers=h, files={"file": ("Factura 001 ñ.pdf", pdf, "application/pdf")},
                 data={"category": "INVOICE", "versionLabel": "v2"})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert "storageKey" not in doc and doc["mimeType"] == "application/pdf" and doc["versionLabel"] == "v2"
    assert len(storage.bucket.files) == 1
    key = next(iter(storage.bucket.files))
    assert " " not in key and "ñ" not in key  # nombre saneado

    listed = api.get("/api/v1/documents", headers=h, params={"category": "INVOICE"}).json()
    assert any(d["id"] == doc["id"] for d in listed["items"]) and "storageKey" not in str(listed)

    url = api.get(f"/api/v1/documents/{doc['id']}/download-url", headers=h).json()
    assert url["expiresIn"] == 60 and url["url"].startswith("https://storage.test/sign/")

    assert api.delete(f"/api/v1/documents/{doc['id']}", headers=h).status_code == 204
    assert api.get(f"/api/v1/documents/{doc['id']}/download-url", headers=h).status_code == 404
    assert all(d["id"] != doc["id"] for d in api.get("/api/v1/documents", headers=h).json()["items"])

    actions = {e["action"] for e in api.get("/api/v1/audit", headers=h, params={"module": "documents"}).json()["items"]
               if e["entityId"] == doc["id"]}
    assert actions == {"CREATE", "DOWNLOAD", "DELETE"}


def test_document_rejections(api, storage):
    h = auth(login(api, **ADMIN)["accessToken"])
    exe = api.post("/api/v1/documents", headers=h, files={"file": ("a.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
                   data={"category": "OTHER"})
    assert exe.status_code == 415
    empty = api.post("/api/v1/documents", headers=h, files={"file": ("a.pdf", io.BytesIO(b""), "application/pdf")}, data={"category": "OTHER"})
    assert empty.status_code == 400
    bad_rel = api.post("/api/v1/documents", headers=h, files={"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")},
                       data={"category": "OTHER", "relatedCustomerId": str(uuid.uuid4())})
    assert bad_rel.status_code == 400
    assert storage.bucket.files == {}  # nada queda huérfano en Storage si falla el registro


def test_documents_need_permission(api, storage):
    h = auth(login(api, **ADMIN)["accessToken"])
    roles = {r["code"]: r["id"] for r in api.get("/api/v1/roles", headers=h).json()}
    email = f"wh-{uuid.uuid4().hex[:6]}@test.pe"
    api.post("/api/v1/users", headers=h, json={"fullName": "WH", "email": email, "password": "Whouse-12345", "roleId": roles["WAREHOUSE"]})
    wh = auth(login(api, email, "Whouse-12345")["accessToken"])
    assert api.get("/api/v1/documents", headers=wh).status_code == 200  # WAREHOUSE: documents VIEW
    r = api.post("/api/v1/documents", headers=wh, files={"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")}, data={"category": "OTHER"})
    assert r.status_code == 403

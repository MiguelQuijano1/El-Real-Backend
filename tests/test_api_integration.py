import uuid

import pytest

ADMIN = {"email": "admin@test.pe", "password": "AdminPass-12345"}


def login(api, email, password, expect=200):
    r = api.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == expect, r.text
    return r.json()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin_token(api):
    return login(api, **ADMIN)["accessToken"]


def make_user(api, admin_token, role_code, password="UserPass-12345", status=None):
    roles = {r["code"]: r["id"] for r in api.get("/api/v1/roles", headers=auth(admin_token)).json()}
    email = f"{role_code.lower()}-{uuid.uuid4().hex[:8]}@test.pe"
    r = api.post("/api/v1/users", headers=auth(admin_token),
                 json={"fullName": f"Test {role_code}", "email": email, "password": password, "roleId": roles[role_code]})
    assert r.status_code == 201, r.text
    return r.json()


def test_health(api):
    r = api.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["database"] == "connected"


def test_protected_routes_need_a_session(api):
    assert api.get("/api/v1/users").status_code == 401
    assert api.get("/api/v1/customers", headers=auth("garbage")).status_code == 401


def test_login_and_me(api):
    bad = api.post("/api/v1/auth/login", json={"email": "nadie@test.pe", "password": "whatever-123"})
    assert bad.status_code == 401 and bad.json()["message"] == "Invalid email or password"

    data = login(api, **ADMIN)
    assert data["tokenType"] == "Bearer" and "passwordHash" not in str(data)
    assert set(data["permissions"]["customers"]) == {"view", "create", "edit", "delete", "approve"}
    me = api.get("/api/v1/auth/me", headers=auth(data["accessToken"])).json()
    assert me["email"] == ADMIN["email"] and me["role"]["code"] == "ADMIN" and "sessionId" not in me


def test_validation_errors_are_400(api):
    r = api.post("/api/v1/auth/login", json={"email": "no-es-correo", "password": "x"})
    assert r.status_code == 400 and isinstance(r.json()["message"], list)


def test_permissions_deny_by_default(api, admin_token):
    sales = make_user(api, admin_token, "SALES")
    token = login(api, sales["email"], "UserPass-12345")["accessToken"]
    assert api.get("/api/v1/customers", headers=auth(token)).status_code == 200      # SALES: clientes WORK
    assert api.get("/api/v1/users", headers=auth(token)).status_code == 403          # sin users:view
    assert api.get("/api/v1/audit", headers=auth(token)).status_code == 403
    assert api.delete(f"/api/v1/customers/{uuid.uuid4()}", headers=auth(token)).status_code == 403  # sin delete
    perms = login(api, sales["email"], "UserPass-12345")["permissions"]
    assert "users" not in perms and "view" in perms["customers"]


def test_privilege_escalation_blocked(api, admin_token):
    # Rol personalizado que SÍ puede crear/editar usuarios, pero no es Administrador.
    code = f"HR_{uuid.uuid4().hex[:6].upper()}"
    role = api.post("/api/v1/roles", headers=auth(admin_token), json={"code": code, "name": f"RRHH {code}"}).json()
    r = api.put(f"/api/v1/roles/{role['id']}/permissions", headers=auth(admin_token),
                json={"permissions": {"users": ["view", "create", "edit"], "roles": ["view"]}})
    assert r.status_code == 200
    hr_pw = "HrPass-12345"
    roles = {x["code"]: x["id"] for x in api.get("/api/v1/roles", headers=auth(admin_token)).json()}
    email = f"hr-{uuid.uuid4().hex[:6]}@test.pe"
    assert api.post("/api/v1/users", headers=auth(admin_token),
                    json={"fullName": "HR", "email": email, "password": hr_pw, "roleId": role["id"]}).status_code == 201
    hr = login(api, email, hr_pw)["accessToken"]

    # No puede crear un Administrador…
    r = api.post("/api/v1/users", headers=auth(hr),
                 json={"fullName": "X", "email": f"x-{uuid.uuid4().hex[:6]}@test.pe", "password": "Whatever-1234", "roleId": roles["ADMIN"]})
    assert r.status_code == 403
    # …ni tocar a un Administrador existente.
    admin_id = api.get("/api/v1/auth/me", headers=auth(admin_token)).json()["id"]
    assert api.patch(f"/api/v1/users/{admin_id}/status", headers=auth(hr), json={"status": "INACTIVE"}).status_code == 403
    assert api.post(f"/api/v1/users/{admin_id}/reset-password", headers=auth(hr), json={"newPassword": "Hacked-12345"}).status_code == 403
    # Nadie cambia su propio estado ni rol.
    me = api.get("/api/v1/auth/me", headers=auth(hr)).json()["id"]
    assert api.patch(f"/api/v1/users/{me}/role", headers=auth(hr), json={"roleId": roles["ADMIN"]}).status_code == 403
    assert api.patch(f"/api/v1/users/{me}/status", headers=auth(hr), json={"status": "INACTIVE"}).status_code == 403


def test_admin_role_matrix_is_immutable(api, admin_token):
    roles = {x["code"]: x["id"] for x in api.get("/api/v1/roles", headers=auth(admin_token)).json()}
    r = api.put(f"/api/v1/roles/{roles['ADMIN']}/permissions", headers=auth(admin_token), json={"permissions": {}})
    assert r.status_code == 400
    r = api.put(f"/api/v1/roles/{roles['SALES']}/permissions", headers=auth(admin_token), json={"permissions": {"nope": ["view"]}})
    assert r.status_code == 400


def test_lockout_and_unlock(api, admin_token):
    user = make_user(api, admin_token, "SALES")
    for _ in range(5):
        login(api, user["email"], "wrong-password-1", expect=401)
    locked = api.post("/api/v1/auth/login", json={"email": user["email"], "password": "UserPass-12345"})
    assert locked.status_code == 401 and locked.json()["code"] == "ACCOUNT_LOCKED"

    assert api.patch(f"/api/v1/users/{user['id']}/status", headers=auth(admin_token), json={"status": "ACTIVE"}).status_code == 200
    login(api, user["email"], "UserPass-12345")


def test_sessions_logout_and_password_change(api, admin_token):
    user = make_user(api, admin_token, "SALES")
    t1 = login(api, user["email"], "UserPass-12345")["accessToken"]
    t2 = login(api, user["email"], "UserPass-12345")["accessToken"]

    assert api.post("/api/v1/auth/change-password", headers=auth(t1),
                    json={"currentPassword": "UserPass-12345", "newPassword": "UserPass-12345"}).status_code == 400
    assert api.post("/api/v1/auth/change-password", headers=auth(t1),
                    json={"currentPassword": "wrong-one-123", "newPassword": "NewPass-123456"}).status_code == 400
    assert api.post("/api/v1/auth/change-password", headers=auth(t1),
                    json={"currentPassword": "UserPass-12345", "newPassword": "NewPass-123456"}).status_code == 204
    assert api.get("/api/v1/auth/me", headers=auth(t1)).status_code == 200      # la sesión actual sigue
    assert api.get("/api/v1/auth/me", headers=auth(t2)).status_code == 401      # las demás se cerraron

    assert api.post("/api/v1/auth/logout", headers=auth(t1)).status_code == 204
    assert api.get("/api/v1/auth/me", headers=auth(t1)).status_code == 401
    login(api, user["email"], "NewPass-123456")


def test_reset_password_revokes_sessions(api, admin_token):
    user = make_user(api, admin_token, "SALES")
    t = login(api, user["email"], "UserPass-12345")["accessToken"]
    assert api.post(f"/api/v1/users/{user['id']}/reset-password", headers=auth(admin_token),
                    json={"newPassword": "Reset-123456"}).status_code == 204
    assert api.get("/api/v1/auth/me", headers=auth(t)).status_code == 401
    login(api, user["email"], "Reset-123456")


def test_inactive_user_cannot_use_existing_token(api, admin_token):
    user = make_user(api, admin_token, "SALES")
    t = login(api, user["email"], "UserPass-12345")["accessToken"]
    api.patch(f"/api/v1/users/{user['id']}/status", headers=auth(admin_token), json={"status": "INACTIVE"})
    assert api.get("/api/v1/auth/me", headers=auth(t)).status_code == 401
    denied = api.post("/api/v1/auth/login", json={"email": user["email"], "password": "UserPass-12345"})
    assert denied.status_code == 401 and denied.json()["code"] == "ACCOUNT_INACTIVE"


def test_duplicate_email_conflict(api, admin_token):
    user = make_user(api, admin_token, "SALES")
    roles = {x["code"]: x["id"] for x in api.get("/api/v1/roles", headers=auth(admin_token)).json()}
    r = api.post("/api/v1/users", headers=auth(admin_token),
                 json={"fullName": "Dup", "email": user["email"].upper(), "password": "UserPass-12345", "roleId": roles["SALES"]})
    assert r.status_code == 409


def test_login_rate_limit(api):
    codes = [api.post("/api/v1/auth/login", json={"email": "x@test.pe", "password": "wrong-pass-1"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_customers_crud(api, admin_token):
    h = auth(admin_token)
    ruc = "20" + uuid.uuid4().int.__str__()[:9]
    r = api.post("/api/v1/customers", headers=h, json={"legalName": "Cliente Test SAC", "documentType": "RUC", "documentNumber": ruc})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["code"].startswith("CLI-") and c["status"] == "ACTIVE"

    assert api.post("/api/v1/customers", headers=h, json={"legalName": "X", "documentType": "RUC", "documentNumber": "123"}).status_code == 400
    assert api.post("/api/v1/customers", headers=h, json={"legalName": "Dup", "documentType": "RUC", "documentNumber": ruc}).status_code == 409
    assert api.post("/api/v1/customers", headers=h, json={"legalName": "X", "documentType": "DNI", "documentNumber": "12345678", "hack": 1}).status_code == 400

    found = api.get("/api/v1/customers", headers=h, params={"q": "Cliente Test"}).json()
    assert found["total"] >= 1 and any(i["id"] == c["id"] for i in found["items"])
    assert api.get("/api/v1/customers", headers=h, params={"q": "a,b)(*%"}).status_code == 200  # caracteres de filtro neutralizados

    p = api.patch(f"/api/v1/customers/{c['id']}", headers=h, json={"phone": "999888777", "creditLimit": 5000})
    assert p.status_code == 200 and p.json()["phone"] == "999888777"
    assert api.patch(f"/api/v1/customers/{c['id']}", headers=h, json={}).status_code == 400
    assert api.get("/api/v1/customers/not-a-uuid", headers=h).status_code == 400
    assert api.get(f"/api/v1/customers/{uuid.uuid4()}", headers=h).status_code == 404

    assert api.delete(f"/api/v1/customers/{c['id']}", headers=h).status_code == 204
    assert api.get(f"/api/v1/customers/{c['id']}", headers=h).json()["status"] == "INACTIVE"
    active = api.get("/api/v1/customers", headers=h, params={"status": "ACTIVE", "q": "Cliente Test"}).json()["items"]
    assert all(i["id"] != c["id"] for i in active)  # la baja lógica lo saca del listado de activos

    events = api.get("/api/v1/audit", headers=h, params={"module": "customers"}).json()
    actions = {e["action"] for e in events["items"] if e["entityId"] == c["id"]}
    assert actions == {"CREATE", "EDIT", "DELETE"}


def test_products_and_catalog_masters(api, admin_token):
    h = auth(admin_token)
    cat = api.post("/api/v1/categories", headers=h, json={"name": f"Cemento {uuid.uuid4().hex[:6]}"})
    assert cat.status_code == 201 and cat.json()["code"].startswith("CAT-")
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    pr = api.post("/api/v1/products", headers=h, json={"sku": sku, "name": "Cemento Sol 42.5kg", "unit": "BLS",
                                                       "salePrice": "28.50", "categoryId": cat.json()["id"]})
    assert pr.status_code == 201, pr.text
    assert api.post("/api/v1/products", headers=h, json={"sku": sku + "x", "name": "n", "unit": "u", "salePrice": 1, "averageCost": 9}).status_code == 400
    assert api.post("/api/v1/products", headers=h, json={"sku": sku + "y", "name": "n", "unit": "u", "salePrice": 1,
                                                          "categoryId": str(uuid.uuid4())}).status_code == 400  # FK inexistente
    by_cat = api.get("/api/v1/products", headers=h, params={"category_id": cat.json()["id"]}).json()
    assert by_cat["total"] == 1

    assert api.post("/api/v1/warehouses", headers=h, json={"name": f"Central {uuid.uuid4().hex[:6]}", "warehouseType": "MAIN"}).status_code == 201
    assert api.post("/api/v1/suppliers", headers=h, json={"legalName": "Prov SAC", "taxId": "2" + uuid.uuid4().hex[:9]}).status_code == 201
    assert api.post("/api/v1/vehicles", headers=h, json={"plate": uuid.uuid4().hex[:7].upper(), "vehicleType": "Camión"}).status_code == 201
    assert api.post("/api/v1/drivers", headers=h, json={"fullName": "Juan", "nationalId": uuid.uuid4().hex[:8], "licenseNumber": uuid.uuid4().hex[:9],
                                                         "licenseCategory": "A-IIIb"}).status_code == 201


def test_settings(api, admin_token):
    h = auth(admin_token)
    s = api.get("/api/v1/settings", headers=h).json()
    assert s["maxLoginAttempts"] == 5 and s["currency"] == "PEN"
    assert api.patch("/api/v1/settings", headers=h, json={"maxLoginAttempts": 99}).status_code == 400
    assert api.patch("/api/v1/settings", headers=h, json={"quotationValidityDays": 21}).json()["quotationValidityDays"] == 21
    api.patch("/api/v1/settings", headers=h, json={"quotationValidityDays": 14})


def test_audit_listing_and_catalog(api, admin_token):
    h = auth(admin_token)
    page = api.get("/api/v1/audit", headers=h, params={"module": "security", "action": "LOGIN", "limit": 5}).json()
    assert len(page["items"]) <= 5 and page["total"] >= 1 and all(e["moduleKey"] == "security" for e in page["items"])
    assert api.get("/api/v1/audit", headers=h, params={"action": "NOPE"}).status_code == 400
    assert api.get("/api/v1/permissions/catalog", headers=h).json()["actions"][0] == "view"
    assert len(api.get("/api/v1/permissions", headers=h).json()) == 29 * 5


def test_no_password_hash_ever_leaves_the_api(api, admin_token):
    h = auth(admin_token)
    for path in ("/api/v1/users", "/api/v1/roles", "/api/v1/audit"):
        assert "password" not in api.get(path, headers=h).text.lower()

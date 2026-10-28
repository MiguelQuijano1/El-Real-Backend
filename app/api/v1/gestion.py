"""Rutas de los módulos de Gestión. Cada una reutiliza un permiso que ya existe (no se crean permisos nuevos):

  Interruptores de módulos ... ver: cualquier sesión · cambiar: settings.edit
  Metas / Comisiones ......... ver: reports.view     · cambiar: settings.edit
  Inventario físico .......... ver: adjustments.view · contar / abrir / cerrar: adjustments.create
  Hoja de ruta (entregas) .... ver: sales-orders.view · marcar: sales-orders.edit
  Flujo de caja .............. ver: receivables.view · saldo inicial: cash.edit
"""
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.core.serialization import camel
from app.schemas import gestion as sch
from app.security.deps import AuthUser, authenticated, request_meta, require_permission
from app.services import gestion as svc

router = APIRouter(tags=["gestion"])

_Period = Annotated[str | None, Query(pattern=sch.PERIOD)]

_reports_view = require_permission("reports", "view")
_settings_edit = require_permission("settings", "edit")


# ---- Interruptores de módulos ----
@router.get("/module-switches")
def switches_get(_: AuthUser = Depends(authenticated)):
    return {"switches": svc.get_switches()}


@router.put("/module-switches/{module_id}")
def switches_put(module_id: str, request: Request, dto: sch.SwitchIn = Body(...), actor: AuthUser = Depends(_settings_edit)):
    return {"switches": svc.set_switch(module_id, dto.enabled, actor, request_meta(request))}


# ---- Metas ----
@router.get("/sales-goals")
def goals_list(period: _Period = None, _: AuthUser = Depends(_reports_view)):
    return {"items": camel(svc.list_goals(period))}


@router.post("/sales-goals", status_code=201)
def goals_create(request: Request, dto: sch.GoalCreate = Body(...), actor: AuthUser = Depends(_settings_edit)):
    return camel(svc.create_goal(dto, actor, request_meta(request)))


@router.post("/sales-goals/copy")
def goals_copy(request: Request, dto: sch.GoalCopy = Body(...), actor: AuthUser = Depends(_settings_edit)):
    return camel(svc.copy_goals(dto, actor, request_meta(request)))


@router.patch("/sales-goals/{goal_id}")
def goals_update(goal_id: str, request: Request, dto: sch.GoalUpdate = Body(...), actor: AuthUser = Depends(_settings_edit)):
    return camel(svc.update_goal(goal_id, dto, actor, request_meta(request)))


@router.delete("/sales-goals/{goal_id}", status_code=204)
def goals_delete(goal_id: str, request: Request, actor: AuthUser = Depends(_settings_edit)):
    svc.delete_goal(goal_id, actor, request_meta(request))
    return Response(status_code=204)


# ---- Comisiones ----
def _commissions_out(data: dict) -> dict:
    # by_seller tiene nombres de vendedor como claves: no se pasa por camel() para no alterarlos.
    return {"defaultPct": data["default_pct"], "bySeller": data["by_seller"], "payouts": camel(data["payouts"])}


@router.get("/commissions")
def commissions_get(_: AuthUser = Depends(_reports_view)):
    return _commissions_out(svc.get_commissions())


@router.put("/commissions/default-pct")
def commissions_default(request: Request, dto: sch.DefaultPctIn = Body(...), actor: AuthUser = Depends(_settings_edit)):
    return _commissions_out(svc.set_default_pct(dto, actor, request_meta(request)))


@router.put("/commissions/seller-pct")
def commissions_seller(request: Request, dto: sch.SellerPctIn = Body(...), actor: AuthUser = Depends(_settings_edit)):
    return _commissions_out(svc.set_seller_pct(dto, actor, request_meta(request)))


@router.post("/commissions/payouts", status_code=201)
def commissions_payout(request: Request, dto: sch.PayoutIn = Body(...), actor: AuthUser = Depends(_settings_edit)):
    return _commissions_out(svc.create_payout(dto, actor, request_meta(request)))


# ---- Inventario físico ----
_counts_view = require_permission("adjustments", "view")
_counts_write = require_permission("adjustments", "create")


@router.get("/stock-counts")
def counts_list(limit: Annotated[int, Query(ge=1, le=500)] = 200, _: AuthUser = Depends(_counts_view)):
    return {"items": camel(svc.list_counts(limit))}


@router.post("/stock-counts", status_code=201)
def counts_create(request: Request, dto: sch.CountCreate = Body(...), actor: AuthUser = Depends(_counts_write)):
    return camel(svc.create_count(dto, actor, request_meta(request)))


@router.get("/stock-counts/{count_id}")
def counts_get(count_id: str, _: AuthUser = Depends(_counts_view)):
    return camel(svc.get_count(count_id))


@router.patch("/stock-counts/{count_id}/items/{item_id}")
def counts_set_counted(count_id: str, item_id: str, dto: sch.CountedIn = Body(...), _: AuthUser = Depends(_counts_write)):
    return camel(svc.set_counted(count_id, item_id, dto))


@router.post("/stock-counts/{count_id}/close")
def counts_close(count_id: str, request: Request, actor: AuthUser = Depends(_counts_write)):
    return camel(svc.close_count(count_id, actor, request_meta(request)))


# ---- Hoja de ruta ----
@router.get("/deliveries")
def deliveries_list(
    codes: Annotated[str, Query(max_length=6000, description="Códigos de orden separados por coma")] = "",
    _: AuthUser = Depends(require_permission("sales-orders", "view")),
):
    wanted = list(dict.fromkeys(c.strip() for c in codes.split(",") if c.strip()))[:300]
    return {"items": camel(svc.list_deliveries(wanted))}


@router.put("/deliveries/{order_code}")
def deliveries_put(order_code: str, request: Request, dto: sch.DeliveryIn = Body(...),
                   actor: AuthUser = Depends(require_permission("sales-orders", "edit"))):
    return camel(svc.upsert_delivery(order_code, dto, actor, request_meta(request)))


@router.delete("/deliveries/{order_code}", status_code=204)
def deliveries_delete(order_code: str, request: Request, actor: AuthUser = Depends(require_permission("sales-orders", "edit"))):
    svc.delete_delivery(order_code, actor, request_meta(request))
    return Response(status_code=204)


# ---- Flujo de caja ----
@router.get("/cashflow/settings")
def cashflow_get(_: AuthUser = Depends(require_permission("receivables", "view"))):
    return camel(svc.get_cashflow())


@router.put("/cashflow/settings")
def cashflow_put(request: Request, dto: sch.CashflowIn = Body(...), actor: AuthUser = Depends(require_permission("cash", "edit"))):
    return camel(svc.set_cashflow(dto, actor, request_meta(request)))
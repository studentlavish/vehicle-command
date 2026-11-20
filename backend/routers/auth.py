"""Sign-in routes: password login, Google sign-in, logout, session refresh."""
import secrets as pysecrets
from datetime import datetime, timezone, timedelta

import jwt
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import RedirectResponse

import security
import google_oauth


from core import JWT_ALG, JWT_SECRET, LoginIn, ROLE_ALL, create_access_token, create_refresh_token, db, get_current_user, logger, now_utc, set_auth_cookies, verify_password

router = APIRouter(prefix="/api")

# ===================== Auth Routes =====================
@router.post("/auth/login")
async def login(payload: LoginIn, request: Request, response: Response):
    email = payload.email.lower()
    ip = request.client.host if request.client else ""
    locked = await security.login_locked_for(db, email, ip)
    if locked:
        raise HTTPException(status_code=429, detail=f"Too many failed sign-in attempts. Try again in {locked // 60 + 1} min.",
                            headers={"Retry-After": str(locked)})
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        await security.record_login_failure(db, email, ip)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await security.clear_login_failures(db, email, ip)
    if user.get("status") == "inactive":
        raise HTTPException(status_code=403, detail="Account is inactive")
    if user.get("role") not in ROLE_ALL:
        raise HTTPException(status_code=403, detail="Unknown role")
    uid = str(user["_id"])
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": now_utc().isoformat()}})
    return {
        "id": uid,
        "email": email,
        "name": user.get("name", "User"),
        "role": user.get("role"),
        "token": access,
    }


# ---------- Sign in with Google (direct OAuth, see google_oauth.py) ----------
def _login_redirect(error: str = "") -> RedirectResponse:
    target = google_oauth.public_url() or ""
    return RedirectResponse(f"{target}/login?error={error}" if error else f"{target}/", status_code=303)


@router.get("/auth/config")
async def auth_config():
    """What the login page should offer."""
    return {"google": google_oauth.enabled()}


@router.get("/auth/google/start")
async def google_start():
    if not google_oauth.enabled():
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    flow = google_oauth.new_flow()
    resp = RedirectResponse(google_oauth.authorization_url(flow), status_code=303)
    token = jwt.encode({**flow, "exp": datetime.now(timezone.utc) + timedelta(seconds=google_oauth.FLOW_SECONDS)},
                       JWT_SECRET, algorithm=JWT_ALG)
    resp.set_cookie(google_oauth.FLOW_COOKIE, token, **{**security.cookie_kwargs(google_oauth.FLOW_SECONDS),
                                                        "path": "/api/auth/google"})
    return resp


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Google sends the user back here. Only emails already listed under Users may sign in."""
    def fail(reason: str) -> RedirectResponse:
        r = _login_redirect(reason)
        r.delete_cookie(google_oauth.FLOW_COOKIE, path="/api/auth/google")
        return r

    if error or not code:
        return fail("google_cancelled")
    try:
        flow = jwt.decode(request.cookies.get(google_oauth.FLOW_COOKIE, ""), JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return fail("google_expired")
    if not state or not pysecrets.compare_digest(state, flow.get("state", "")):
        return fail("google_state")
    try:
        claims = await google_oauth.identity_from_code(code, flow)
    except Exception as e:
        logger.warning("[auth] Google sign-in failed: %s", e)
        return fail("google_failed")
    email = claims["email"].strip().lower()
    user = await db.users.find_one({"email": email})
    if not user:
        logger.info("[auth] Google sign-in refused for %s (not under Users)", email)
        return fail("google_not_allowed")
    if user.get("status") == "inactive" or user.get("role") not in ROLE_ALL:
        return fail("google_not_allowed")
    uid = str(user["_id"])
    update = {"last_login": now_utc().isoformat(), "auth_provider": "google"}
    if claims.get("name") and not user.get("name"):
        update["name"] = claims["name"]
    if claims.get("picture"):
        update["picture"] = claims["picture"]
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    await security.audit(db, {"email": email, "role": user.get("role")}, "auth.google_login", email)
    resp = _login_redirect()
    set_auth_cookies(resp, create_access_token(uid, email), create_refresh_token(uid))
    resp.delete_cookie(google_oauth.FLOW_COOKIE, path="/api/auth/google")
    return resp


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/auth/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user or user.get("status") == "inactive":
            raise HTTPException(status_code=401, detail="User not found or inactive")
        access = create_access_token(str(user["_id"]), user["email"])
        response.set_cookie("access_token", access, **security.cookie_kwargs(43200))
        return {"ok": True}
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")



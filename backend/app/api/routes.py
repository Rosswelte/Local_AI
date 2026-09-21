import json
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.errors import AppError
from app.hardware.compatibility import evaluate
from app.orchestrator.jobs import create_job
from app.providers.openai_compatible import validate_headers
from app.services.image_outputs import output_path
from app.services.image_workflows import apply_overrides, list_workflows, load_workflow


router = APIRouter(prefix="/api/v1")


class ConversationIn(BaseModel):
    title: str = "Nouvelle conversation"
    model_id: int | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1)
    force: bool = False
    allow_remote: bool = False


class BehaviorIn(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    system_prompt: str = "Tu es un assistant utile."
    default_model_id: int | None = None
    allowed_tools: list[str] = []
    params: dict[str, Any] = {}


class SettingsIn(BaseModel):
    values: dict[str, Any]


class PinIn(BaseModel):
    pinned: bool = True


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=4096)
    type: str = "llm"
    extra_headers: dict[str, str] = {}


class ImageJobIn(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    prompt: str = Field(min_length=1, max_length=4000)
    seed: int | None = Field(default=None, ge=0)
    steps: int = Field(default=20, ge=1, le=100)
    cfg: float = Field(default=7.0, ge=0, le=30)
    width: int = Field(default=1024, ge=64, le=2048, multiple_of=8)
    height: int = Field(default=1024, ge=64, le=2048, multiple_of=8)


def state(request: Request):
    return request.app.state


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    if not getattr(request.app.state, "ready", False):
        raise AppError("not_ready", "Le service n'est pas prêt", 503)
    return {"status": "ok"}


@router.get("/hardware")
async def hardware(request: Request):
    return request.app.state.profile.as_dict()


@router.post("/hardware/detect")
async def detect_hardware(request: Request):
    from app.hardware.detector import detect_profile
    profile = detect_profile()
    request.app.state.profile = profile
    await request.app.state.db.write(_save_profile, profile)
    await request.app.state.resources.update_budgets({"ram_mb": profile.ram_budget_mb, "vram_mb": profile.vram_budget_mb})
    return profile.as_dict()


@router.get("/models")
async def models(request: Request):
    rows = await request.app.state.db.read(lambda con: [dict(row) for row in con.execute("SELECT * FROM models ORDER BY id").fetchall()])
    loaded = set()
    try:
        loaded = {item.get("name") or item.get("model") for item in await request.app.state.ollama.loaded_models()}
    except Exception:
        pass
    result = []
    for row in rows:
        row["installed"] = bool(row["installed"])
        row["enabled"] = bool(row["enabled"])
        row["pinned"] = bool(row["pinned"])
        row["loaded"] = row["name"] in loaded
        row["fit"] = evaluate(row, request.app.state.profile)
        row["perf"] = json.loads(row["perf"] or "{}")
        row["config"] = json.loads(row["config"] or "{}")
        row["capabilities"] = json.loads(row.get("capabilities") or "[]")
        row["spec"] = json.loads(row.get("spec") or "{}")
        result.append(row)
    return result


@router.get("/services")
async def services(request: Request):
    return await request.app.state.db.read(lambda con: [
        {key: value for key, value in dict(row).items() if key not in {"api_key_enc", "extra_headers_enc"}}
        for row in con.execute("SELECT * FROM services ORDER BY id")
    ])


@router.get("/image/workflows")
async def image_workflows():
    return list_workflows()


@router.post("/image/jobs")
async def create_image_job(payload: ImageJobIn, request: Request):
    if "comfyui" not in request.app.state.providers:
        raise AppError("provider_unavailable", "Le provider ComfyUI n'est pas configuré", 503)
    try:
        workflow = load_workflow(payload.workflow_id)
    except (FileNotFoundError, KeyError, json.JSONDecodeError, ValueError) as exc:
        raise AppError("workflow_not_found", "Workflow ComfyUI introuvable", 404) from exc
    workflow = apply_overrides(
        workflow,
        prompt=payload.prompt,
        seed=payload.seed,
        steps=payload.steps,
        cfg=payload.cfg,
        width=payload.width,
        height=payload.height,
    )
    job_id = await request.app.state.db.write(
        create_job,
        kind="image",
        provider="comfyui",
        input_data={"workflow_id": payload.workflow_id, "workflow": workflow},
    )
    await request.app.state.jobs.enqueue()
    return {"job_id": job_id, "workflow_id": payload.workflow_id}


@router.get("/image/jobs/{job_id}/outputs")
async def image_outputs(job_id: int, request: Request):
    job = await request.app.state.db.read(lambda con, value: con.execute("SELECT id, kind, state, output FROM jobs WHERE id=?", (value,)).fetchone(), job_id)
    if not job:
        raise AppError("not_found", "Job introuvable", 404)
    if job["kind"] != "image":
        raise AppError("invalid_job", "Ce job n'est pas un job image", 409)
    return {"job_id": job_id, "state": job["state"], "outputs": json.loads(job["output"] or "[]")}


@router.get("/image/jobs/{job_id}/outputs/{output_id}")
async def image_output_file(job_id: int, output_id: int, request: Request):
    job = await request.app.state.db.read(lambda con, value: con.execute("SELECT kind, output FROM jobs WHERE id=?", (value,)).fetchone(), job_id)
    if not job:
        raise AppError("not_found", "Job introuvable", 404)
    if job["kind"] != "image":
        raise AppError("invalid_job", "Ce job n'est pas un job image", 409)
    metadata = next((item for item in json.loads(job["output"] or "[]") if int(item.get("id", -1)) == output_id), None)
    if not metadata:
        raise AppError("not_found", "Sortie image introuvable", 404)
    path = output_path(request.app.state.settings.data_dir, job_id, metadata)
    if not path.is_file():
        raise AppError("not_found", "Fichier image introuvable", 404)
    return FileResponse(path, media_type=metadata["media_type"], filename=metadata["name"])


@router.get("/services/status")
async def service_status(request: Request):
    return await request.app.state.service_manager.check()


@router.get("/settings")
async def settings(request: Request):
    from app.services.settings import read_settings
    return await request.app.state.db.read(read_settings)


@router.put("/settings")
async def update_settings(payload: SettingsIn, request: Request):
    from app.services.settings import read_settings, write_settings
    await request.app.state.db.write(write_settings, payload.values)
    configured = await request.app.state.db.read(read_settings)
    profile = request.app.state.profile
    await request.app.state.resources.update_budgets({
        "ram_mb": int(configured["ram_budget_mb"]["value"] if configured["ram_budget_mb"]["value"] is not None else profile.ram_budget_mb),
        "vram_mb": int(configured["vram_budget_mb"]["value"] if configured["vram_budget_mb"]["value"] is not None else profile.vram_budget_mb),
    })
    return configured


@router.get("/behaviors")
async def behaviors(request: Request):
    return await request.app.state.db.read(lambda con: [_behavior(row) for row in con.execute("SELECT * FROM behaviors ORDER BY id")])


@router.post("/behaviors")
async def create_behavior(payload: BehaviorIn, request: Request):
    def insert(con):
        cursor = con.execute("INSERT INTO behaviors(name, description, system_prompt, default_model_id, allowed_tools, params) VALUES (?, ?, ?, ?, ?, ?)", (payload.name, payload.description, payload.system_prompt, payload.default_model_id, json.dumps(payload.allowed_tools), json.dumps(payload.params)))
        return cursor.lastrowid
    behavior_id = await request.app.state.db.write(insert)
    return {"id": behavior_id, "name": payload.name, "description": payload.description, "system_prompt": payload.system_prompt, "default_model_id": payload.default_model_id, "allowed_tools": payload.allowed_tools, "params": payload.params}


@router.post("/services")
async def create_service(payload: ServiceIn, request: Request):
    _validate_service_url(payload.url)
    try:
        validate_headers(payload.extra_headers, payload.api_key)
    except ValueError as exc:
        raise AppError("validation_error", str(exc), 422) from exc

    def insert(con):
        service_id = con.execute(
            "INSERT INTO services(name, type, url, is_remote, api_key_enc, extra_headers_enc) VALUES (?, ?, ?, 1, ?, ?)",
            (payload.name, payload.type, payload.url.rstrip("/"), request.app.state.secrets.encrypt(payload.api_key), request.app.state.secrets.encrypt(json.dumps(payload.extra_headers))),
        ).lastrowid
        model_id = con.execute(
            "INSERT INTO models(name, label, display_name, provider, service_id, type, capabilities, installed) VALUES (?, ?, ?, 'openai_compatible', ?, ?, '[]', 1)",
            (payload.model, payload.model, payload.model, service_id, payload.type),
        ).lastrowid
        return service_id, model_id

    try:
        service_id, model_id = await request.app.state.db.write(insert)
    except Exception as exc:
        raise AppError("validation_error", "Impossible de créer ce service", 422) from exc
    row = await request.app.state.db.read(lambda con: dict(con.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()))
    try:
        provider = await request.app.state.service_manager.register_remote_service(row)
        status = "online" if await provider.health() else "offline"
    except Exception as exc:
        status = "offline"
        provider = None
        await request.app.state.db.write(lambda con: con.execute("UPDATE services SET last_status='offline', last_check_at=CURRENT_TIMESTAMP WHERE id=?", (service_id,)))
        if provider is None:
            raise AppError("provider_unreachable", "Le service est enregistré mais injoignable", 502) from exc
    await request.app.state.db.write(lambda con, value: con.execute("UPDATE services SET last_status=?, last_check_at=CURRENT_TIMESTAMP WHERE id=?", (value, service_id)), status)
    return {"id": service_id, "model_id": model_id, "name": payload.name, "url": payload.url, "type": payload.type, "status": status}


@router.post("/services/{service_id}/test")
async def test_service(service_id: int, request: Request):
    row = await request.app.state.db.read(lambda con: con.execute("SELECT * FROM services WHERE id=? AND is_remote=1", (service_id,)).fetchone())
    if not row:
        raise AppError("not_found", "Service distant introuvable", 404)
    provider = request.app.state.providers.get(request.app.state.service_manager.provider_key(service_id))
    if not provider:
        provider = await request.app.state.service_manager.register_remote_service(dict(row))
    try:
        online = bool(await provider.health())
    except Exception:
        online = False
    status = "online" if online else "offline"
    await request.app.state.db.write(lambda con: con.execute("UPDATE services SET last_status=?, last_check_at=CURRENT_TIMESTAMP WHERE id=?", (status, service_id)))
    return {"service_id": service_id, "status": status}


@router.delete("/services/{service_id}")
async def delete_service(service_id: int, request: Request):
    active = await request.app.state.db.read(lambda con: con.execute("SELECT id FROM jobs WHERE model_id IN (SELECT id FROM models WHERE service_id=?) AND state IN ('queued', 'running') LIMIT 1", (service_id,)).fetchone())
    if active:
        raise AppError("resource_busy", "Le service est utilisé par un job actif", 409, {"job_id": active["id"]})
    row = await request.app.state.db.read(lambda con: con.execute("SELECT id FROM services WHERE id=? AND is_remote=1", (service_id,)).fetchone())
    if not row:
        raise AppError("not_found", "Service distant introuvable", 404)
    provider = request.app.state.providers.pop(request.app.state.service_manager.provider_key(service_id), None)
    if provider and hasattr(provider, "close"):
        await provider.close()
    await request.app.state.db.write(lambda con: con.execute("DELETE FROM services WHERE id=?", (service_id,)))
    return {"deleted": service_id}


@router.post("/models/sync")
async def sync_models(request: Request):
    try:
        installed = await request.app.state.ollama.installed_models()
    except Exception as exc:
        raise AppError("provider_offline", "Ollama est indisponible", 503) from exc
    names = {item.get("name") for item in installed}
    await request.app.state.db.write(lambda con: _sync_installed(con, names))
    return {"installed": sorted(names)}


@router.post("/models/{model_id}/load")
async def load_model(model_id: int, request: Request):
    model = await _model(request, model_id)
    if model["provider"] != "ollama":
        raise AppError("provider_unavailable", "Le préchargement de ce provider n'est pas disponible", 409)
    if not model["installed"]:
        raise AppError("model_not_installed", "Installez d'abord ce modèle", 409)
    try:
        estimate = request.app.state.ollama.estimate(model)
        await request.app.state.resources.load_model("ollama", model, estimate, bool(model["pinned"]))
    except Exception as exc:
        raise AppError("provider_offline", "Impossible de charger le modèle", 503) from exc
    return {"status": "loaded", "model_id": model_id}


@router.post("/models/{model_id}/unload")
async def unload_model(model_id: int, request: Request):
    model = await _model(request, model_id)
    if model["provider"] != "ollama":
        raise AppError("provider_unavailable", "Le déchargement de ce provider n'est pas disponible", 409)
    try:
        await request.app.state.resources.unload("ollama", model["name"])
    except Exception as exc:
        raise AppError("provider_offline", "Impossible de décharger le modèle", 503) from exc
    return {"status": "unloaded", "model_id": model_id}


@router.post("/models/{model_id}/pin")
async def pin_model(model_id: int, payload: PinIn, request: Request):
    model = await _model(request, model_id)
    provider_name = _provider_name(model, request)
    await request.app.state.db.write(lambda con: con.execute("UPDATE models SET pinned=? WHERE id=?", (int(payload.pinned), model_id)))
    await request.app.state.resources.set_pinned(provider_name, model["name"], payload.pinned)
    return {"model_id": model_id, "pinned": payload.pinned}


@router.delete("/models/{model_id}/install")
async def delete_model(model_id: int, request: Request):
    model = await _model(request, model_id)
    if model["provider"] != "ollama":
        raise AppError("provider_unavailable", "La suppression de ce provider n'est pas disponible", 409)
    active = await request.app.state.db.read(lambda con: con.execute("SELECT id FROM jobs WHERE model_id=? AND state IN ('queued', 'running') LIMIT 1", (model_id,)).fetchone())
    if active:
        raise AppError("resource_busy", "Le modèle est utilisé par un job actif", 409, {"job_id": active["id"]})
    try:
        await request.app.state.ollama.delete(model["name"])
    except Exception as exc:
        raise AppError("provider_offline", "Impossible de supprimer le modèle", 503) from exc
    await request.app.state.db.write(lambda con: con.execute("UPDATE models SET installed=0 WHERE id=?", (model_id,)))
    return {"status": "deleted", "model_id": model_id}


@router.post("/models/{model_id}/pull")
async def pull_model(model_id: int, request: Request):
    model = await _model(request, model_id)
    if model["provider"] != "ollama":
        raise AppError("provider_unavailable", "Ce modèle n'est pas géré par Ollama", 409)
    job_id = await request.app.state.db.write(create_job, kind="pull", provider="ollama", model_id=model_id, priority=5)
    await request.app.state.jobs.enqueue()
    return {"job_id": job_id}


@router.post("/conversations")
async def create_conversation(payload: ConversationIn, request: Request):
    def insert(con):
        cursor = con.execute("INSERT INTO conversations(title, model_id) VALUES (?, ?)", (payload.title, payload.model_id))
        return cursor.lastrowid
    conversation_id = await request.app.state.db.write(insert)
    return {"id": conversation_id, "title": payload.title, "model_id": payload.model_id}


@router.get("/conversations")
async def list_conversations(request: Request):
    return await request.app.state.db.read(lambda con: [dict(row) for row in con.execute("SELECT * FROM conversations ORDER BY updated_at DESC, id DESC").fetchall()])


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: int, request: Request):
    return await request.app.state.db.read(lambda con: [dict(row) for row in con.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()])


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: int, payload: MessageIn, request: Request):
    def insert(con):
        conversation = con.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not conversation:
            raise AppError("not_found", "Conversation introuvable", 404)
        if conversation["model_id"] is None:
            raise AppError("model_required", "Sélectionnez un modèle pour cette conversation", 422)
        model = con.execute("SELECT * FROM models WHERE id=?", (conversation["model_id"],)).fetchone()
        if not model:
            raise AppError("not_found", "Modèle introuvable", 404)
        if not model["enabled"]:
            raise AppError("model_disabled", "Le modèle est désactivé", 409)
        if not model["installed"]:
            raise AppError("model_not_installed", "Installez d'abord ce modèle", 409)
        provider_name = _provider_name(dict(model), request)
        if model["service_id"] is not None:
            remote_setting = con.execute("SELECT value FROM settings WHERE key='remote_warn_before_send'").fetchone()
            remote_warning = True if not remote_setting or remote_setting["value"] is None else bool(json.loads(remote_setting["value"]))
            if remote_warning and not payload.allow_remote:
                raise AppError("confirmation_required", "Cette requête enverra vos données à un service distant", 409, {"remote": True})
        fit = evaluate(dict(model), request.app.state.profile)
        if fit["level"] == "impossible":
            raise AppError("fit_impossible", "Les ressources minimales détectées sont insuffisantes", 409, {"missing_mb": fit["missing_mb"]})
        if fit["level"] == "not_recommended" and not payload.force:
            raise AppError("confirmation_required", "Ce modèle nécessite une confirmation", 409, {"fit": fit})
        user = con.execute("INSERT INTO messages(conversation_id, role, content, status) VALUES (?, 'user', ?, 'complete')", (conversation_id, payload.content))
        assistant = con.execute("INSERT INTO messages(conversation_id, role, content, status) VALUES (?, 'assistant', '', 'streaming')", (conversation_id,)).lastrowid
        guard = int(fit["level"] == "not_recommended" and payload.force)
        job = con.execute("INSERT INTO jobs(kind, provider, model_id, message_id, force, memory_guard) VALUES ('text', ?, ?, ?, ?, ?)", (provider_name, conversation["model_id"], assistant, int(payload.force), guard)).lastrowid
        con.execute("UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
        return {"message_id": assistant, "job_id": job}
    result = await request.app.state.db.write(insert)
    await request.app.state.jobs.enqueue()
    return result


@router.get("/jobs")
async def list_jobs(request: Request, status: str | None = None):
    states = [item.strip() for item in status.split(",")] if status else []
    def query(con):
        if states:
            placeholders = ",".join("?" for _ in states)
            return [dict(row) for row in con.execute(f"SELECT * FROM jobs WHERE state IN ({placeholders}) ORDER BY id DESC", states).fetchall()]
        return [dict(row) for row in con.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()]
    return await request.app.state.db.read(query)


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, request: Request):
    job = await request.app.state.db.read(lambda con: con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    if not job:
        raise AppError("not_found", "Job introuvable", 404)
    return dict(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, request: Request):
    await request.app.state.jobs.cancel(job_id)
    return {"status": "cancel_requested"}


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: int, request: Request, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    job = await request.app.state.db.read(lambda con: con.execute("SELECT id, state, error_code, error_message FROM jobs WHERE id=?", (job_id,)).fetchone())
    if not job:
        raise AppError("not_found", "Job introuvable", 404)
    after = int(last_event_id or 0)

    async def body():
        sent = False
        async for event in request.app.state.jobs.events.subscribe(job_id, after):
            sent = True
            yield f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        if not sent and job["state"] in {"completed", "failed", "cancelled"} and not await request.app.state.jobs.events.has_events(job_id):
            event_type = {"completed": "completed", "cancelled": "cancelled", "failed": "error"}[job["state"]]
            data = {} if event_type != "error" else {"code": job["error_code"], "message": job["error_message"]}
            yield f"id: 1\nevent: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _model(request: Request, model_id: int):
    row = await request.app.state.db.read(lambda con: con.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone())
    if not row:
        raise AppError("not_found", "Modèle introuvable", 404)
    return dict(row)


def _provider_name(model: dict[str, Any], request: Request) -> str:
    if model.get("service_id") is not None:
        return request.app.state.service_manager.provider_key(int(model["service_id"]))
    return "ollama"


def _save_profile(con, profile):
    gpu = profile.gpu
    con.execute("""INSERT INTO machine_profile(id, ram_total_mb, ram_available_mb, cpu_cores, gpu_name, gpu_total_mb,
        gpu_free_mb, container_ram_limit_mb, ram_budget_mb, vram_budget_mb, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET ram_total_mb=excluded.ram_total_mb, ram_available_mb=excluded.ram_available_mb,
        cpu_cores=excluded.cpu_cores, gpu_name=excluded.gpu_name, gpu_total_mb=excluded.gpu_total_mb,
        gpu_free_mb=excluded.gpu_free_mb, container_ram_limit_mb=excluded.container_ram_limit_mb,
        ram_budget_mb=excluded.ram_budget_mb, vram_budget_mb=excluded.vram_budget_mb, updated_at=CURRENT_TIMESTAMP""",
        (profile.ram_total_mb, profile.ram_available_mb, profile.cpu_cores, gpu.name if gpu else None,
         gpu.total_mb if gpu else 0, gpu.free_mb if gpu else 0, profile.container_ram_limit_mb,
         profile.ram_budget_mb, profile.vram_budget_mb))


def _sync_installed(con, names):
    con.execute("UPDATE models SET installed=0")
    for name in names:
        con.execute("UPDATE models SET installed=1 WHERE name=?", (name,))


def _behavior(row):
    item = dict(row)
    item["allowed_tools"] = json.loads(item["allowed_tools"] or "[]")
    item["params"] = json.loads(item["params"] or "{}")
    item["active"] = bool(item["active"])
    return item


def _validate_service_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError("validation_error", "URL de service invalide", 422)
    host = parsed.hostname.lower()
    private = host in {"localhost", "127.0.0.1", "::1"} or host.startswith(("10.", "192.168.", "172.16."))
    if parsed.scheme != "https" and not private:
        raise AppError("validation_error", "HTTPS est requis pour un hôte public", 422)

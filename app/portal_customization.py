"""Persisted developer portal drafts, publication, and operator editing."""

from __future__ import annotations

import base64
import binascii
import uuid
import zlib
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PortalPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(default="", max_length=20000)


def _valid_png(data: bytes) -> bool:
    if len(data) <= 32 or not data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"):
        return False
    offset = 8
    image_data = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        end = offset + length + 12
        if end > len(data):
            return False
        chunk = data[offset + 4 : end - 4]
        if zlib.crc32(chunk) != int.from_bytes(data[end - 4 : end], "big"):
            return False
        kind = chunk[:4]
        image_data |= kind == b"IDAT"
        if kind == b"IEND":
            return length == 0 and image_data and end == len(data)
        offset = end
    return False


class PortalMediaUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    content_base64: str = Field(max_length=2796204)

    @model_validator(mode="after")
    def validate_image(self) -> PortalMediaUpload:
        try:
            data = base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Image must use valid base64") from exc
        if not data or len(data) > 2 * 1024 * 1024:
            raise ValueError("Image must be no larger than 2 MiB")
        formats = {
            "image/png": _valid_png(data),
            "image/jpeg": len(data) > 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"),
            "image/webp": len(data) > 16
            and data.startswith(b"RIFF")
            and data[8:12] == b"WEBP"
            and int.from_bytes(data[4:8], "little") == len(data) - 8,
        }
        if not formats[self.content_type]:
            raise ValueError("Image bytes must match the selected PNG, JPEG, or WebP format")
        return self


class PortalMedia(PortalMediaUpload):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")


class PortalSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_title: str = Field(default="APIM Simulator Developer Portal", min_length=1, max_length=160)
    logo_url: str | None = Field(default=None, max_length=2048)
    accent_color: str = Field(default="#12705f", pattern=r"^#[0-9a-fA-F]{6}$")
    background_color: str = Field(default="#f2ede1", pattern=r"^#[0-9a-fA-F]{6}$")
    background_image_url: str | None = Field(default=None, max_length=2048)
    theme: Literal["light", "dark"] = "light"
    media: list[PortalMedia] = Field(default_factory=list, max_length=10)
    pages: list[PortalPage] = Field(
        default_factory=lambda: [
            PortalPage(
                slug="home",
                title="Developer Portal",
                content="Browse published products, request a subscription, and try API calls against the local simulator.",
            )
        ],
        min_length=1,
        max_length=30,
    )

    @field_validator("logo_url", "background_image_url")
    @classmethod
    def safe_image_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
            raise ValueError("Image URL must be an HTTP(S) URL or a local absolute path")
        parts = urlsplit(value)
        local_path = value.startswith("/") and not value.startswith("//")
        remote_url = parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username
        if not (local_path or remote_url):
            raise ValueError("Image URL must be an HTTP(S) URL or a local absolute path")
        return value

    @model_validator(mode="after")
    def unique_pages_with_home(self) -> PortalSite:
        slugs = [page.slug for page in self.pages]
        if len(slugs) != len(set(slugs)):
            raise ValueError("Page slugs must be unique")
        if "home" not in slugs:
            raise ValueError("A home page is required")
        if len({item.id for item in self.media}) != len(self.media):
            raise ValueError("Media IDs must be unique")
        return self


class PortalContentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: PortalSite = Field(default_factory=PortalSite)
    published: PortalSite = Field(default_factory=PortalSite)
    publication: int = Field(default=0, ge=0)


def build_portal_customization_router(*, require_management_plane: Callable[[], Any]) -> APIRouter:  # noqa: C901 - route declarations; see docs/complexity.md
    # Imports here avoid a config -> persisted model -> config cycle.
    from app.portal import render_portal_page
    from app.security import require_tenant_access

    router = APIRouter()

    def config(request: Request, *, operator: bool = False) -> Any:
        cfg = request.app.state.gateway_config
        if not cfg.portal.enabled:
            raise HTTPException(status_code=404, detail="Portal is not enabled")
        if operator:
            require_tenant_access(request)
        return cfg

    @router.get("/apim/portal/editor", response_class=HTMLResponse)
    async def editor(request: Request) -> HTMLResponse:
        config(request)
        # The login shell has no draft data; every data endpoint requires a tenant key.
        return HTMLResponse(PORTAL_EDITOR_HTML)

    @router.get("/apim/portal/content")
    async def published(request: Request) -> dict[str, Any]:
        content = config(request).portal_content
        return {"publication": content.publication, "site": content.published.model_dump(mode="json")}

    @router.get("/apim/portal/pages/{slug}", response_class=HTMLResponse)
    async def page(request: Request, slug: str) -> HTMLResponse:
        return HTMLResponse(render_portal_page(config(request).portal_content.published, slug=slug))

    @router.get("/apim/portal/media/{media_id}")
    async def public_media(request: Request, media_id: str) -> Response:
        site = config(request).portal_content.published
        media = next((item for item in site.media if item.id == media_id), None)
        if media is None:
            raise HTTPException(status_code=404, detail="Portal media not found")
        return Response(
            base64.b64decode(media.content_base64),
            media_type=media.content_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @router.post("/apim/portal/editor/media", status_code=201)
    async def upload_media(request: Request, body: PortalMediaUpload) -> dict[str, str]:
        cfg = config(request, operator=True).model_copy(deep=True)
        if len(cfg.portal_content.draft.media) >= 10:
            raise HTTPException(status_code=409, detail="Media library is limited to 10 images")
        media = PortalMedia(id="image-" + uuid.uuid4().hex, **body.model_dump())
        cfg.portal_content.draft.media.append(media)
        require_management_plane().persist_or_apply_config(cfg)
        return {"id": media.id, "name": media.name, "url": "/apim/portal/media/" + media.id}

    @router.get("/apim/portal/editor/draft")
    async def draft(request: Request) -> dict[str, Any]:
        content = config(request, operator=True).portal_content
        return {"publication": content.publication, "site": content.draft.model_dump(mode="json")}

    @router.put("/apim/portal/editor/draft")
    async def save_draft(request: Request, body: PortalSite) -> dict[str, Any]:
        cfg = config(request, operator=True).model_copy(deep=True)
        cfg.portal_content.draft = body.model_copy(deep=True)
        saved = require_management_plane().persist_or_apply_config(cfg)
        return {
            "publication": saved.portal_content.publication,
            "site": saved.portal_content.draft.model_dump(mode="json"),
        }

    @router.post("/apim/portal/editor/publish")
    async def publish(request: Request) -> dict[str, Any]:
        cfg = config(request, operator=True).model_copy(deep=True)
        cfg.portal_content.published = cfg.portal_content.draft.model_copy(deep=True)
        cfg.portal_content.publication += 1
        saved = require_management_plane().persist_or_apply_config(cfg)
        return {
            "publication": saved.portal_content.publication,
            "site": saved.portal_content.published.model_dump(mode="json"),
        }

    @router.get("/apim/portal/editor/preview", response_class=HTMLResponse)
    async def preview(request: Request, slug: str = "home") -> HTMLResponse:
        site = config(request, operator=True).portal_content.draft
        return HTMLResponse(render_portal_page(site, slug=slug), headers={"Cache-Control": "no-store"})

    return router


PORTAL_EDITOR_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Developer Portal Editor</title>
<style>
:root{color-scheme:light;background:#f2ede1;color:#1a1711;font-family:Arial,sans-serif}
main{max-width:960px;margin:auto;padding:2rem 1rem}section{padding:1.4rem;background:#fffaf2;border:1px solid #ccc;border-radius:16px;margin-bottom:1rem}
h1{font-size:1.7rem}label{display:block;margin:.7rem 0}input,select,textarea,button{font:inherit;border:1px solid #aaa;border-radius:6px;padding:.5rem}input:not([type=color]),textarea{display:block;width:100%;max-width:640px}
button{background:#12705f;color:white;cursor:pointer;margin:.3rem}textarea{min-height:130px}iframe{width:100%;height:560px;border:1px solid #ccc;border-radius:10px}.row{display:flex;gap:1rem;flex-wrap:wrap}
</style></head><body><main><h1>Developer Portal Editor</h1>
<p>Save your draft, preview it, and publish when it is ready for visitors.</p>
<section><h2>Operator access</h2><label>Tenant key<input id="tenant-key" type="password" autocomplete="off"></label>
<button id="load">Open saved draft</button><a href="/apim/portal" target="_blank" rel="noopener">View published portal</a></section>
<section id="settings" hidden><h2>Brand and styles</h2>
<h3>Media library</h3><label>Upload image (PNG, JPEG, or WebP; up to 2 MiB)<input id="media-file" type="file" accept="image/png,image/jpeg,image/webp"></label>
<button id="upload-media">Upload to draft library</button><label>Saved images<select id="media-select"></select></label>
<button id="logo-media">Use as logo</button><button id="background-media">Use as background</button>
<label>Site title<input id="site-title" maxlength="160"></label>
<label>Logo image URL<input id="logo-url" placeholder="https://example.com/logo.png"></label>
<label>Home background image URL<input id="background-image-url" placeholder="https://example.com/background.png"></label>
<div class="row"><label>Primary color<input id="accent-color" type="color"></label>
<label>Background color<input id="background-color" type="color"></label>
<label>Theme<select id="theme"><option>light</option><option>dark</option></select></label></div>
<h2>Pages and navigation</h2><label>Page<select id="page-select"></select></label>
<label>Page title<input id="page-title" maxlength="160"></label>
<label>Page text<textarea id="page-content"></textarea></label>
<label>New page address<input id="new-slug" placeholder="getting-started" pattern="[a-z0-9][a-z0-9-]{0,63}"></label>
<button id="add-page">Add page</button><button id="remove-page">Remove page</button>
<p>Pages use plain text. Page order controls the shared navigation menu.</p>
<button id="save">Save draft</button><button id="preview">Preview saved draft</button><button id="publish">Publish saved draft</button>
</section><p id="status" role="status" aria-live="polite"></p><iframe id="preview-frame" title="Saved draft preview" sandbox="" hidden></iframe>
</main><script>
let site = null, selected = 'home';
const byId = id => document.getElementById(id);
const status = message => { byId('status').textContent = message; };
async function api(path, options = {}) {
  const response = await fetch('/apim/portal/editor/' + path, {...options,
    headers: {'X-Apim-Tenant-Key': byId('tenant-key').value, 'Content-Type':'application/json'}});
  if (!response.ok) { const data = await response.json(); throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)); }
  return response;
}
function rememberPage() {
  const page = site.pages.find(page => page.slug === selected);
  page.title = byId('page-title').value; page.content = byId('page-content').value;
}
function showPage() {
  const page = site.pages.find(page => page.slug === selected);
  byId('page-title').value = page.title; byId('page-content').value = page.content;
  byId('remove-page').disabled = selected === 'home';
}
function showMenu() {
  byId('page-select').replaceChildren(...site.pages.map(page => {
    const option = document.createElement('option'); option.value = page.slug; option.textContent = page.title; return option;
  })); byId('page-select').value = selected; showPage();
}
function showMedia() {
  byId('media-select').replaceChildren(...site.media.map(media => {
    const option = document.createElement('option'); option.value = '/apim/portal/media/' + media.id; option.textContent = media.name; return option;
  }));
}
async function load() {
  const data = await (await api('draft')).json(); site = data.site; selected = 'home';
  for (const key of ['site_title','logo_url','accent_color','background_color','background_image_url','theme']) byId(key.replaceAll('_','-')).value = site[key] ?? '';
  byId('settings').hidden = false; showMenu(); showMedia(); status('Loaded saved draft. Publication ' + data.publication + '.');
}
async function save() {
  rememberPage(); for (const key of ['site_title','logo_url','accent_color','background_color','background_image_url','theme']) site[key] = byId(key.replaceAll('_','-')).value || null;
  const data = await (await api('draft', {method:'PUT',body:JSON.stringify(site)})).json(); site = data.site; showMenu(); status('Draft saved. Visitors still see the published version.');
}
async function preview() {
  byId('preview-frame').srcdoc = await (await api('preview?slug=' + encodeURIComponent(selected))).text();
  byId('preview-frame').hidden = false; status('Preview of saved draft.');
}
async function publish() {
  const data = await (await api('publish', {method:'POST'})).json(); status('Published version ' + data.publication + '. Open the published portal to view it.');
}
async function uploadMedia() {
  const file = byId('media-file').files[0];
  if (!file || !['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 2 * 1024 * 1024) throw new Error('Choose a PNG, JPEG, or WebP image up to 2 MiB.');
  const dataUrl = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('Unable to read image.')); reader.readAsDataURL(file); });
  const content = {name:file.name, content_type:file.type, content_base64:dataUrl.split(',')[1]};
  const media = await (await api('media', {method:'POST',body:JSON.stringify(content)})).json();
  site.media.push({...content,id:media.id}); showMedia(); byId('media-select').value = media.url;
  status('Image saved to draft library. Choose it as a logo or background, then save and publish.');
}
function addPage() {
  const slug = byId('new-slug').value;
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(slug) || site.pages.some(page => page.slug === slug)) throw new Error('Choose a unique page address using lowercase letters, digits, and hyphens.');
  rememberPage(); site.pages.push({slug, title:'New page', content:''}); selected = slug; showMenu();
}
function removePage() { if (selected === 'home') return; site.pages = site.pages.filter(page => page.slug !== selected); selected = 'home'; showMenu(); }
for (const [id, action] of [['load',load],['save',save],['preview',preview],['publish',publish],['add-page',addPage],['remove-page',removePage],['upload-media',uploadMedia]]) byId(id).onclick = async () => { try { await action(); } catch (error) { status(error.message); } };
byId('logo-media').onclick = () => { if (byId('media-select').value) byId('logo-url').value = byId('media-select').value; };
byId('background-media').onclick = () => { if (byId('media-select').value) byId('background-image-url').value = byId('media-select').value; };
byId('page-select').onchange = () => { rememberPage(); selected = byId('page-select').value; showPage(); };
</script></body></html>"""

from copy import deepcopy
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from app.errors import AppError
from app.schemas import CreateCampaign, EditOffer
from app.store import Store
from app.weights import distribute, validate_publish


def offer_signature(stream: dict) -> tuple:
    """Ignore link IDs/timestamps: Keitaro may regenerate them on a PUT."""
    return (
        stream.get("schema"),
        sorted(
            (int(o["offer_id"]), int(o["share"]), o.get("state", "active"))
            for o in stream.get("offers", [])
        ),
    )


def changed(stream: dict) -> bool:
    return stream["offers"] != stream["baseline"]


def present(document: dict) -> dict:
    result = deepcopy(document)
    for stream in result["streams"]:
        stream["dirty"] = changed(stream)
        stream["total"] = sum(o["share"] for o in stream["offers"] if o["state"] == "active")
        del stream["baseline"]
        del stream["remote"]
    result["dirty_count"] = sum(s["dirty"] for s in result["streams"])
    return result


class CampaignService:
    def __init__(self, store: Store, keitaro):
        self.store = store
        self.keitaro = keitaro
        self._offer_cache: tuple[float, list[dict]] = (-float("inf"), [])

    def offers(self, query: str = "", offset: int = 0, limit: int = 30):
        if monotonic() - self._offer_cache[0] > 30:
            offers = self.keitaro.get("offers")
            self._offer_cache = (monotonic(), offers)
        query = query.casefold().strip()
        matches = [
            {"id": o["id"], "name": o["name"]}
            for o in self._offer_cache[1]
            if o.get("state") == "active"
            and (not query or query in str(o["id"]) or query in o["name"].casefold())
        ]
        matches.sort(key=lambda o: (str(o["id"]) != query, -o["id"]))
        return {"items": matches[offset : offset + limit], "total": len(matches)}

    def metadata(self):
        result = {}
        for key, path in (
            ("domains", "domains"),
            ("groups", "groups?type=campaigns"),
            ("traffic_sources", "traffic_sources"),
        ):
            result[key] = [
                {"id": item["id"], "name": item["name"]}
                for item in self.keitaro.get(path)
                if item.get("state", "active") == "active"
            ]
        return result

    def campaigns(self, query: str, offset: int, limit: int):
        items = self.keitaro.get("campaigns")
        query = query.strip().casefold()
        items = [
            {key: c.get(key) for key in ("id", "name", "state", "domain", "group")}
            for c in items
            if c.get("state") != "deleted"
            and (not query or query in str(c["id"]) or query in c["name"].casefold())
        ]
        return {"items": items[offset : offset + limit], "total": len(items)}

    def _offer(self, offer_id: int) -> dict:
        offer = self.keitaro.get(f"offers/{offer_id}")
        if offer.get("state") != "active":
            raise AppError("Этот оффер недоступен. Выберите активный оффер.")
        return {
            "offer_id": offer["id"],
            "name": offer["name"],
            "state": "active",
            "share": 0,
            "pinned": False,
        }

    def create(self, request: CreateCampaign) -> dict:
        metadata = self.metadata()
        for key, value in (
            ("domains", request.domain_id),
            ("groups", request.group_id),
            ("traffic_sources", request.traffic_source_id),
        ):
            if value not in {item["id"] for item in metadata[key]}:
                raise AppError(f"Выбранный элемент {key} больше недоступен.")
        self._offer(request.offer_id)
        campaign = self.keitaro.post(
            "campaigns",
            {
                "name": request.name,
                "alias": f"adrobot-{uuid4().hex[:16]}",
                "type": "position",
                "state": "disabled",
                "cost_type": "CPC",
                "cost_value": 0,
                "domain_id": request.domain_id,
                "group_id": request.group_id,
                "traffic_source_id": request.traffic_source_id,
            },
        )
        campaign_id = campaign["id"]
        try:
            self.keitaro.post(
                "streams",
                {
                    "campaign_id": campaign_id,
                    "name": f"Geo {', '.join(request.geo)} → Google",
                    "type": "regular",
                    "position": 1,
                    "state": "active",
                    "schema": "action",
                    "action_type": "http",
                    "action_payload": "https://www.google.com/",
                    "collect_clicks": True,
                    "filter_or": False,
                    "filters": [{"name": "country", "mode": "accept", "payload": request.geo}],
                },
            )
            self.keitaro.post(
                "streams",
                {
                    "campaign_id": campaign_id,
                    "name": "Offers",
                    "type": "regular",
                    "position": 2,
                    "state": "active",
                    "schema": "landings",
                    "action_type": "http",
                    "collect_clicks": True,
                    "offers": [{"offer_id": request.offer_id, "share": 100, "state": "active"}],
                },
            )
            self.keitaro.put(f"campaigns/{campaign_id}", {"state": "active"})
        except AppError as exc:
            try:
                self.keitaro.request("DELETE", f"campaigns/{campaign_id}")
            except AppError:
                raise AppError(
                    f"Создание кампании #{campaign_id} прервано. Автоархивация не удалась. "
                    "Проверьте её состояние в Keitaro.",
                    502,
                ) from exc
            raise AppError(
                f"Создание прервано; неполная кампания #{campaign_id} перемещена в архив. "
                f"{exc.message}",
                502,
            ) from exc
        # A sync failure must not archive a successfully created, complete campaign.
        try:
            return self.sync(campaign_id)
        except AppError as exc:
            raise AppError(
                f"Кампания #{campaign_id} создана, но загрузить её не удалось. "
                "Найдите её по ID и нажмите «Загрузить из Keitaro».",
                502,
            ) from exc

    def sync(self, campaign_id: int, revision: int | None = None) -> dict:
        with self.store.transaction() as db:
            try:
                old = self.store.load(db, campaign_id)
            except AppError:
                old = {"id": campaign_id, "streams": [], "revision": 0}
            if old["revision"]:
                if revision is None:
                    raise AppError("Кампания уже загружена. Откройте её перед синхронизацией.", 409)
                self.store.check_revision(old, revision)
            if any(changed(s) for s in old["streams"]):
                raise AppError("Сначала опубликуйте или отмените черновики потоков.", 409)
            campaign = self.keitaro.get(f"campaigns/{campaign_id}")
            remote_streams = self.keitaro.get(f"campaigns/{campaign_id}/streams")
            # Use the bounded offer catalogue for display names, never expose raw offers/tokens.
            self.offers()
            names = {o["id"]: o["name"] for o in self._offer_cache[1]}
            old_streams = {s["id"]: s for s in old["streams"]}
            streams = []
            for remote in remote_streams:
                previous = old_streams.pop(remote["id"], None)
                known = {o["offer_id"]: o for o in previous["offers"]} if previous else {}
                offers = []
                for offer in remote.get("offers", []):
                    prior = known.pop(offer["offer_id"], {})
                    offers.append(
                        {
                            "offer_id": offer["offer_id"],
                            "name": names.get(
                                offer["offer_id"], prior.get("name", f"Оффер #{offer['offer_id']}")
                            ),
                            "share": int(offer["share"]),
                            "state": offer.get("state", "active"),
                            "pinned": prior.get("pinned", False) and offer.get("state") == "active",
                        }
                    )
                for offer in known.values():
                    offers.append({**offer, "state": "removed", "share": 0, "pinned": False})
                streams.append(
                    {
                        "id": remote["id"],
                        "name": remote.get("name") or f"Поток #{remote['id']}",
                        "position": remote["position"],
                        "schema": remote["schema"],
                        "state": remote.get("state", "active"),
                        "missing": False,
                        "editable": remote["schema"] == "landings",
                        "offers": offers,
                        "baseline": deepcopy(offers),
                        "remote": remote,
                    }
                )
            for stream in old_streams.values():
                stream["missing"] = True
                stream["editable"] = False
                streams.append(stream)
            document = {
                "id": campaign_id,
                "name": campaign["name"],
                "state": campaign.get("state", "active"),
                "domain": campaign.get("domain"),
                "group": campaign.get("group"),
                "revision": old["revision"],
                "admin_url": f"{self.keitaro.client.base_url.scheme}://"
                f"{self.keitaro.client.base_url.netloc.decode()}/admin/#!/campaigns/{campaign_id}",
                "synced_at": datetime.now(UTC).isoformat(),
                "streams": sorted(streams, key=lambda s: s["position"]),
            }
            return present(self.store.save(db, document))

    @staticmethod
    def _stream(document: dict, stream_id: int) -> dict:
        stream = next((s for s in document["streams"] if s["id"] == stream_id), None)
        if stream is None:
            raise AppError("Поток не найден.", 404)
        if not stream["editable"] or stream["missing"]:
            raise AppError("Этот поток доступен только для просмотра.")
        return stream

    def edit(self, campaign_id: int, stream_id: int, request: EditOffer) -> dict:
        with self.store.transaction() as db:
            document = self.store.load(db, campaign_id)
            self.store.check_revision(document, request.revision)
            stream = self._stream(document, stream_id)
            offers = stream["offers"]
            offer = next((o for o in offers if o["offer_id"] == request.offer_id), None)
            if request.action == "add":
                if offer is not None:
                    raise AppError("Оффер уже есть в потоке или архиве. Используйте «Вернуть».")
                offers.append(self._offer(request.offer_id))
            elif offer is None:
                raise AppError("Оффер не найден в потоке.", 404)
            elif request.action == "remove":
                if not any(o["offer_id"] == offer["offer_id"] for o in stream["baseline"]):
                    offers.remove(offer)
                else:
                    offer.update(state="removed", share=0, pinned=False)
            elif request.action == "restore":
                self._offer(request.offer_id)
                offer.update(state="active", pinned=False)
            elif request.action == "pin":
                if offer["state"] != "active":
                    raise AppError("Закрепить вес можно только у активного оффера.")
                offer.update(pinned=True, share=request.share or offer["share"])
            elif request.action == "unpin":
                offer["pinned"] = False
            distribute(offers)
            return present(self.store.save(db, document))

    def cancel(self, campaign_id: int, stream_id: int, revision: int) -> dict:
        with self.store.transaction() as db:
            document = self.store.load(db, campaign_id)
            self.store.check_revision(document, revision)
            stream = self._stream(document, stream_id)
            stream["offers"] = deepcopy(stream["baseline"])
            return present(self.store.save(db, document))

    def publish(self, campaign_id: int, stream_id: int, revision: int) -> dict:
        with self.store.transaction() as db:
            document = self.store.load(db, campaign_id)
            self.store.check_revision(document, revision)
            stream = self._stream(document, stream_id)
            if not changed(stream):
                raise AppError("В потоке нет изменений.")
            validate_publish(stream["offers"])
            current = self.keitaro.get(f"streams/{stream_id}")
            if offer_signature(current) != offer_signature(stream["remote"]):
                raise AppError(
                    "Офферы или схема потока изменены в Keitaro. "
                    "Отмените черновик и загрузите актуальные потоки перед повтором.",
                    409,
                )
            active_ids = [o["offer_id"] for o in stream["offers"] if o["state"] == "active"]
            # Check restored/added offers still exist before making the single remote write.
            for offer_id in active_ids:
                self._offer(offer_id)
            payload = [
                {"offer_id": o["offer_id"], "share": o["share"], "state": o["state"]}
                for o in stream["offers"]
                if o["state"] != "removed"
            ]
            updated = self.keitaro.put(f"streams/{stream_id}", {"offers": payload})
            expected = {"schema": current["schema"], "offers": payload}
            if offer_signature(updated) != offer_signature(expected):
                raise AppError(
                    "Keitaro сохранил неожиданные веса. Проверьте поток в админке, "
                    "затем отмените черновик и синхронизируйте данные.",
                    502,
                )
            stream["remote"] = updated
            stream["baseline"] = deepcopy(stream["offers"])
            return present(self.store.save(db, document))

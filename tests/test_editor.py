from copy import deepcopy

from app.service import CampaignService, present
from app.store import Store


def stream(document):
    return next(s for s in document["streams"] if s["id"] == 20)


def edit(client, document, action, offer_id, **kwargs):
    return client.post(
        "/api/campaigns/1/streams/20/offers",
        json={
            "revision": document["revision"],
            "action": action,
            "offer_id": offer_id,
            **kwargs,
        },
    )


def command(client, document, action):
    return client.post(
        f"/api/campaigns/1/streams/20/{action}", json={"revision": document["revision"]}
    )


def test_add_is_a_persistent_draft_and_cancel_restores_snapshot(client, synced, tracker, settings):
    original = deepcopy(tracker.streams)
    document = edit(client, synced, "add", 3).json()
    assert [o["share"] for o in stream(document)["offers"]] == [34, 33, 33]
    assert stream(document)["dirty"]
    assert tracker.streams == original
    reopened = CampaignService(Store(settings.database_path), None)
    assert present(reopened.store.get(1)) == document
    document = command(client, document, "cancel").json()
    assert stream(document)["offers"] == stream(synced)["offers"]
    assert not stream(document)["dirty"]


def test_published_archive_restore_and_pin(client, synced, tracker):
    document = edit(client, synced, "add", 3).json()
    document = edit(client, document, "pin", 1, share=25).json()
    assert [o["share"] for o in stream(document)["offers"]] == [25, 38, 37]
    document = command(client, document, "publish").json()
    assert not stream(document)["dirty"]
    document = edit(client, document, "remove", 3).json()
    assert [o["share"] for o in stream(document)["offers"]] == [25, 75, 0]
    document = command(client, document, "publish").json()
    assert [o["offer_id"] for o in tracker.streams[20]["offers"]] == [1, 2]
    assert stream(document)["offers"][2]["state"] == "removed"
    document = edit(client, document, "restore", 3).json()
    assert [o["share"] for o in stream(document)["offers"]] == [25, 38, 37]
    document = command(client, document, "publish").json()
    assert len(tracker.streams[20]["offers"]) == 3
    assert tracker.streams[20]["filters"] == [{"name": "country", "payload": ["AU"]}]
    assert tracker.streams[20]["landings"] == [{"landing_id": 50, "share": 100}]
    assert tracker.streams[10]["action_payload"] == "https://www.google.com/"
    puts = [c for c in tracker.calls if c[0] == "PUT" and c[1] == "streams/20"]
    assert all(set(call[2]) == {"offers"} for call in puts)


def test_sync_archives_externally_removed_offers_and_detects_new_ones(client, synced, tracker):
    tracker.streams[20]["offers"] = [
        {"offer_id": 1, "share": 75, "state": "active"},
        {"offer_id": 3, "share": 25, "state": "active"},
    ]
    response = client.post("/api/campaigns/1/sync", json={"revision": synced["revision"]})
    document = response.json()
    offers = {o["offer_id"]: o for o in stream(document)["offers"]}
    assert offers[2]["state"] == "removed" and offers[2]["share"] == 0
    assert offers[3]["state"] == "active"
    assert not stream(document)["dirty"]
    document = edit(client, document, "restore", 2).json()
    assert stream(document)["total"] == 100


def test_sync_does_not_recalculate_remote_weights(client, synced, tracker):
    tracker.streams[20]["offers"][0]["share"] = 20
    tracker.streams[20]["offers"][1]["share"] = 80
    document = client.post("/api/campaigns/1/sync", json={"revision": synced["revision"]}).json()
    assert [o["share"] for o in stream(document)["offers"]] == [20, 80]


def test_stale_revision_cannot_overwrite_a_draft(client, synced):
    edit(client, synced, "add", 3).raise_for_status()
    assert edit(client, synced, "remove", 1).status_code == 409
    assert command(client, synced, "cancel").status_code == 409


def test_external_conflict_blocks_publish_without_remote_write(client, synced, tracker):
    document = edit(client, synced, "add", 3).json()
    tracker.streams[20]["offers"][0]["share"] = 40
    assert command(client, document, "publish").status_code == 409
    assert not any(c[0] == "PUT" for c in tracker.calls)
    assert stream(client.get("/api/campaigns/1").json())["dirty"]


def test_failed_publish_retains_draft_and_cancel_works(client, synced, tracker):
    document = edit(client, synced, "add", 3).json()
    tracker.fail_put = True
    assert command(client, document, "publish").status_code == 502
    assert client.get("/api/campaigns/1").json() == document
    assert command(client, document, "cancel").status_code == 200


def test_sync_refuses_to_discard_drafts(client, synced):
    document = edit(client, synced, "add", 3).json()
    assert (
        client.post("/api/campaigns/1/sync", json={"revision": document["revision"]}).status_code
        == 409
    )
    assert client.post("/api/campaigns/1/sync", json=None).status_code == 409
    assert client.get("/api/campaigns/1").json() == document


def test_cannot_publish_zero_active_offers(client, synced, tracker):
    document = edit(client, synced, "remove", 1).json()
    document = edit(client, document, "remove", 2).json()
    assert stream(document)["total"] == 0
    assert command(client, document, "publish").status_code == 400
    assert not any(c[0] == "PUT" for c in tracker.calls)


def test_bad_pin_and_duplicate_leave_document_unchanged(client, synced):
    document = edit(client, synced, "pin", 1, share=80).json()
    assert edit(client, document, "pin", 2, share=30).status_code == 400
    assert client.get("/api/campaigns/1").json() == document
    assert edit(client, document, "add", 1).status_code == 400
    assert edit(client, document, "pin", 2, share=20.5).status_code == 422


def test_new_offer_removed_before_publish_leaves_no_archive(client, synced):
    document = edit(client, synced, "add", 3).json()
    document = edit(client, document, "remove", 3).json()
    assert stream(document)["offers"] == stream(synced)["offers"]
    assert not stream(document)["dirty"]


def test_deleted_stream_is_read_only(client, synced, tracker):
    del tracker.streams[20]
    document = client.post("/api/campaigns/1/sync", json={"revision": synced["revision"]}).json()
    assert stream(document)["missing"]
    assert edit(client, document, "add", 3).status_code == 400


def test_redirect_stream_cannot_be_edited(client, synced):
    response = client.post(
        "/api/campaigns/1/streams/10/offers",
        json={
            "revision": synced["revision"],
            "action": "add",
            "offer_id": 3,
        },
    )
    assert response.status_code == 400


def test_archived_remote_offer_cannot_be_added(client, synced, tracker):
    tracker.offers[3]["state"] = "deleted"
    assert edit(client, synced, "add", 3).status_code == 400
    assert client.get("/api/campaigns/1").json() == synced


def test_pin_survives_publish_and_sync(client, synced):
    document = edit(client, synced, "pin", 1, share=25).json()
    document = command(client, document, "publish").json()
    document = client.post("/api/campaigns/1/sync", json={"revision": document["revision"]}).json()
    assert stream(document)["offers"][0]["pinned"]
    assert stream(document)["offers"][0]["share"] == 25

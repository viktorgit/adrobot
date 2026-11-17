"""Optional integration check: writes only to its own new campaign, then archives it."""

import argparse
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.keitaro import Keitaro
from app.schemas import CreateCampaign, EditOffer
from app.service import CampaignService
from app.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-test-writes",
        action="store_true",
        help="Allow creating, editing and archiving a new test campaign",
    )
    args = parser.parse_args()
    if not args.confirm_test_writes:
        parser.error("Pass --confirm-test-writes to run against the configured tracker")
    settings = Settings()
    api = Keitaro(settings.keitaro_base_url, settings.keitaro_api_key)
    service = CampaignService(Store(Path("artifacts/live-smoke.sqlite3")), api)
    campaign_id = None
    try:
        meta = service.metadata()
        offers = service.offers(limit=3)["items"]
        assert len(offers) == 3, "Need three active offers for this check"
        group = next((g for g in meta["groups"] if g["name"] == "FORTESTS"), meta["groups"][0])
        document = service.create(
            CreateCampaign(
                name=f"test_viktor_adrobot_{uuid4().hex[:8]}",
                geo=["AU"],
                offer_id=offers[0]["id"],
                domain_id=meta["domains"][0]["id"],
                group_id=group["id"],
                traffic_source_id=meta["traffic_sources"][0]["id"],
            )
        )
        campaign_id = document["id"]
        print(f"Created test campaign #{campaign_id}", flush=True)
        remote = api.get(f"campaigns/{campaign_id}/streams")
        assert len(remote) == 2
        geo, flow = sorted(remote, key=lambda s: s["position"])
        assert geo["filters"][0]["payload"] == ["AU"]
        assert geo["action_payload"] == "https://www.google.com/"
        flow_id = flow["id"]

        def edit(action, index, share=None):
            nonlocal document
            document = service.edit(
                campaign_id,
                flow_id,
                EditOffer(
                    revision=document["revision"],
                    action=action,
                    offer_id=offers[index]["id"],
                    share=share,
                ),
            )

        def publish():
            nonlocal document
            document = service.publish(campaign_id, flow_id, document["revision"])

        def local_flow():
            return next(s for s in document["streams"] if s["id"] == flow_id)

        edit("add", 1)
        edit("add", 2)
        assert [o["share"] for o in local_flow()["offers"]] == [34, 33, 33]
        assert len(api.get(f"streams/{flow_id}")["offers"]) == 1
        document = service.cancel(campaign_id, flow_id, document["revision"])
        assert len(local_flow()["offers"]) == 1
        edit("add", 1)
        edit("add", 2)
        publish()
        edit("remove", 2)
        publish()
        assert len(api.get(f"streams/{flow_id}")["offers"]) == 2
        assert local_flow()["offers"][2]["state"] == "removed"
        edit("restore", 2)
        edit("pin", 0, 25)
        publish()
        assert [o["share"] for o in api.get(f"streams/{flow_id}")["offers"]] == [25, 38, 37]
        api.put(
            f"streams/{flow_id}",
            {
                "offers": [
                    {"offer_id": offers[0]["id"], "share": 25, "state": "active"},
                    {"offer_id": offers[1]["id"], "share": 75, "state": "active"},
                ]
            },
        )
        document = service.sync(campaign_id, document["revision"])
        assert local_flow()["offers"][2]["state"] == "removed"
        edit("restore", 2)
        assert [o["share"] for o in local_flow()["offers"]] == [25, 38, 37]
        publish()
        assert api.get(f"streams/{geo['id']}") == geo
        print(
            "PASS: creator, drafts, cancel, publish, archive, restore, pin, external sync",
            flush=True,
        )
    finally:
        if campaign_id is not None:
            api.request("DELETE", f"campaigns/{campaign_id}")
            print(f"Archived test campaign #{campaign_id}", flush=True)
        api.close()


if __name__ == "__main__":
    main()

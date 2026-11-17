"""Integer percentages with deterministic remainder allocation (sum is exactly 100)."""

from app.errors import AppError


def distribute(offers: list[dict]) -> None:
    active = [offer for offer in offers if offer["state"] == "active"]
    for offer in offers:
        if offer["state"] != "active":
            offer["share"] = 0
    fixed = [offer for offer in active if offer["pinned"]]
    free = [offer for offer in active if not offer["pinned"]]
    remaining = 100 - sum(offer["share"] for offer in fixed)
    if remaining < 0:
        raise AppError("Сумма закреплённых весов не может превышать 100%.")
    if active and not free and remaining:
        raise AppError("Все веса закреплены, но сумма не равна 100%. Открепите один вес.")
    if free:
        if remaining < len(free):
            raise AppError(
                "Для каждого активного оффера нужен хотя бы 1%. Уменьшите закреплённые веса."
            )
        share, remainder = divmod(remaining, len(free))
        for index, offer in enumerate(free):
            offer["share"] = share + (index < remainder)


def validate_publish(offers: list[dict]) -> None:
    active = [offer for offer in offers if offer["state"] == "active"]
    if not active:
        raise AppError("В потоке должен остаться хотя бы один активный оффер.")
    if sum(offer["share"] for offer in active) != 100:
        raise AppError("Сумма активных весов должна быть равна 100%.")
    if any(not 1 <= offer["share"] <= 100 for offer in active):
        raise AppError("Вес активного оффера должен быть от 1 до 100%.")

#!/usr/bin/env python3
"""
Report generator for contract payments.
Builds a report showing for each work: price, paid, remaining;
and for each contract: totals.
"""

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional


REPORT_DATE = date(2026, 11, 10)
DATA_DIR = Path(__file__).parent / "data"


@dataclass
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str
    completed_at: Optional[date]
    cancelled_at: Optional[date]


@dataclass
class Payment:
    payment_id: str
    contract_id: str
    work_id: Optional[str]
    purpose: str
    amount: Decimal
    status: str
    created_at: date
    paid_at: Optional[date]
    refunded_at: Optional[date]


def parse_decimal(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


def load_contracts() -> dict[str, Contract]:
    contracts = {}
    with open(DATA_DIR / "contracts.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            contracts[row["contract_id"]] = Contract(
                contract_id=row["contract_id"],
                contractor=row["contractor"],
                amount=parse_decimal(row["amount"]),
                signed_at=parse_date(row["signed_at"]) or date.min,
            )
    return contracts


def load_works() -> dict[str, Work]:
    works = {}
    with open(DATA_DIR / "works.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            works[row["work_id"]] = Work(
                work_id=row["work_id"],
                contract_id=row["contract_id"],
                title=row["title"],
                price=parse_decimal(row["price"]),
                status=row["status"],
                completed_at=parse_date(row["completed_at"]),
                cancelled_at=parse_date(row["cancelled_at"]),
            )
    return works


def load_payments() -> list[Payment]:
    payments = []
    with open(DATA_DIR / "payments.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            work_id = row["work_id"] if row["work_id"] else None
            payments.append(Payment(
                payment_id=row["payment_id"],
                contract_id=row["contract_id"],
                work_id=work_id,
                purpose=row["purpose"],
                amount=parse_decimal(row["amount"]),
                status=row["status"],
                created_at=parse_date(row["created_at"]) or date.min,
                paid_at=parse_date(row["paid_at"]),
                refunded_at=parse_date(row["refunded_at"]),
            ))
    return payments


def get_payments_for_report(payments: list[Payment], report_date: date) -> list[Payment]:
    """
    Filter payments to include only those that count as paid by report_date.
    - status == 'paid' and paid_at <= report_date
    - Exclude: queued, refunded (net zero), future paid
    """
    result = []
    for p in payments:
        if p.status != "paid":
            continue
        if p.paid_at and p.paid_at <= report_date:
            result.append(p)
    return result


def allocate_payments_to_works(
    works: dict[str, Work],
    payments: list[Payment],
    contract_id: str,
    report_date: date,
) -> dict[str, Decimal]:
    """
    Allocate contract-level payments to works using FIFO by completion order.
    Works with specific work_id payments get those first.
    Cancelled works get 0 allocation.
    """
    # Filter works for this contract
    contract_works = [w for w in works.values() if w.contract_id == contract_id]

    # Sort works: done first (by completed_at), then planned, cancelled last
    def work_sort_key(w: Work):
        if w.status == "done":
            return (0, w.completed_at or date.max)
        elif w.status == "planned":
            return (1, w.work_id)
        else:  # cancelled
            return (2, w.cancelled_at or date.max)

    contract_works.sort(key=work_sort_key)

    # Separate payments: specific to work vs contract-level
    work_specific_payments: dict[str, Decimal] = {}
    contract_level_payments: list[Payment] = []

    for p in payments:
        if p.work_id and p.work_id in works:
            work_specific_payments[p.work_id] = work_specific_payments.get(p.work_id, Decimal("0")) + p.amount
        else:
            contract_level_payments.append(p)

    # Total contract-level amount available for allocation
    total_contract_amount = sum(p.amount for p in contract_level_payments)

    # Allocate to works (skip cancelled)
    allocated: dict[str, Decimal] = {}
    remaining = total_contract_amount

    for work in contract_works:
        if work.status == "cancelled":
            allocated[work.work_id] = Decimal("0")
            continue

        # Start with work-specific payments
        specific = work_specific_payments.get(work.work_id, Decimal("0"))
        allocated[work.work_id] = specific

    # Now distribute contract-level payments FIFO to non-cancelled works
    for work in contract_works:
        if work.status == "cancelled":
            continue

        already_allocated = allocated.get(work.work_id, Decimal("0"))
        need = work.price - already_allocated

        if need <= 0:
            continue

        give = min(need, remaining)
        allocated[work.work_id] = already_allocated + give
        remaining -= give

        if remaining <= 0:
            break

    return allocated


def generate_report():
    contracts = load_contracts()
    works = load_works()
    all_payments = load_payments()

    # Filter payments for report date
    report_payments = get_payments_for_report(all_payments, REPORT_DATE)

    # Group payments by contract
    payments_by_contract: dict[str, list[Payment]] = {}
    for p in report_payments:
        payments_by_contract.setdefault(p.contract_id, []).append(p)

    # Output
    print(f"Отчёт на {REPORT_DATE.isoformat()}")
    print("=" * 80)

    for contract in sorted(contracts.values(), key=lambda c: c.contract_id):
        print(f"\nДоговор {contract.contract_id} — {contract.contractor}")
        print(f"Сумма договора: {contract.amount:,.2f} ₽".replace(",", " "))
        print("-" * 80)

        contract_works = [w for w in works.values() if w.contract_id == contract.contract_id]
        contract_works.sort(key=lambda w: w.work_id)

        contract_payments = payments_by_contract.get(contract.contract_id, [])
        allocated = allocate_payments_to_works(works, contract_payments, contract.contract_id, REPORT_DATE)

        total_paid = Decimal("0")
        total_price = Decimal("0")
        total_remaining = Decimal("0")

        print(f"{'Работа':<25} {'Цена':>12} {'Выплачено':>12} {'Осталось':>12} {'Статус':<10}")
        print("-" * 80)

        for work in contract_works:
            paid = allocated.get(work.work_id, Decimal("0"))
            remaining = work.price - paid
            if work.status == "cancelled":
                remaining = Decimal("0")

            total_paid += paid
            total_price += work.price
            total_remaining += remaining

            status_display = {
                "done": "выполнена",
                "planned": "план",
                "cancelled": "отменена",
            }.get(work.status, work.status)

            print(f"{work.title:<25} {work.price:>12,.2f} {paid:>12,.2f} {remaining:>12,.2f} {status_display:<10}".replace(",", " "))

        print("-" * 80)
        print(f"{'ИТОГ по договору':<25} {total_price:>12,.2f} {total_paid:>12,.2f} {total_remaining:>12,.2f}".replace(",", " "))

    print("\n" + "=" * 80)
    print("Примечания:")
    print("- Платежи со статусом 'queued' не включены (деньги ещё не ушли).")
    print("- Возвращённые платежи (refunded) не учитываются в выплаченном.")
    print("- Платежи после даты отчёта (10.11.2026) не включены.")
    print("- Распределение договоровых платежей: FIFO по выполненным работам.")


if __name__ == "__main__":
    generate_report()
from __future__ import annotations

import hashlib
import shutil
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment

from ..models import Invoice, QuarterlySettlement


def _copy_row_style(sheet, source_row: int, target_row: int, max_col: int = 25) -> None:
    sheet.row_dimensions[target_row].height = sheet.row_dimensions[source_row].height
    for col in range(1, max_col + 1):
        source = sheet.cell(source_row, col)
        target = sheet.cell(target_row, col)
        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        target.alignment = copy(source.alignment)


def export_settlements_to_template(
    *,
    settlements: list[QuarterlySettlement],
    template_path: Path,
    output_path: Path,
    invoices_by_settlement: dict[int, Invoice] | None = None,
) -> Path:
    if not template_path.exists():
        raise FileNotFoundError(f"Excel模板不存在：{template_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, output_path)
    workbook = load_workbook(output_path)
    if "利润20%" not in workbook.sheetnames:
        raise ValueError("Excel模板缺少“利润20%”工作表")
    sheet = workbook["利润20%"]
    invoices_by_settlement = invoices_by_settlement or {}

    # 清理模板中的示例数据和开发备注，但保留全部格式、列宽、打印
    # 设置及“Defer 延付利息（待确认）”工作表原样。
    for (row, col), cell in list(sheet._cells.items()):
        if row >= 3 and col <= 25 and cell.value is not None:
            cell.value = None

    export_lines = [
        (settlement, line)
        for settlement in settlements
        for line in (
            settlement.account_lines
            if settlement.calculation_mode == "ACCOUNT_HWM"
            and all(item.service_fee_cents is not None for item in settlement.account_lines)
            else [None]
        )
    ]
    for index, (settlement, account_line) in enumerate(export_lines, start=1):
        row = index + 2
        _copy_row_style(sheet, 3, row)
        invoice = invoices_by_settlement.get(settlement.id)
        account_numbers = (
            account_line.account.account_number
            if account_line is not None
            else "\n".join(line.account.account_number for line in settlement.account_lines)
        )
        beginning_cents = account_line.beginning_cents if account_line is not None else settlement.beginning_cents
        contribution_cents = account_line.contribution_cents if account_line is not None else settlement.contribution_cents
        withdrawal_cents = account_line.withdrawal_cents if account_line is not None else settlement.withdrawal_cents
        closing_cents = account_line.closing_cents if account_line is not None else settlement.closing_cents
        original_hwm_cents = account_line.original_hwm_cents if account_line is not None else settlement.original_hwm_cents
        service_fee_cents = account_line.service_fee_cents if account_line is not None else settlement.service_fee_cents
        sheet.cell(row, 1, index)
        company = settlement.company or settlement.client.company
        fc = settlement.fc or settlement.client.fc
        sheet.cell(row, 2, company.name if company else "")
        sheet.cell(row, 3, f"{settlement.year} Q{settlement.quarter}")
        sheet.cell(row, 4, invoice.invoice_number if invoice else "")
        sheet.cell(row, 5, settlement.client.name)
        sheet.cell(row, 6, fc.name if fc else "")
        sheet.cell(row, 7, settlement.platform.name)
        sheet.cell(row, 8, account_numbers)
        sheet.cell(row, 9, account_line.start_date if account_line is not None else settlement.start_date)
        sheet.cell(row, 10, account_line.closing_date if account_line is not None else settlement.closing_date)
        sheet.cell(row, 11, f"=J{row}-I{row}+1")
        sheet.cell(row, 12, int(beginning_cents) / 100)
        sheet.cell(row, 13, int(contribution_cents or 0) / 100)
        sheet.cell(row, 14, int(withdrawal_cents or 0) / 100)
        sheet.cell(row, 15, f"=M{row}-N{row}")
        sheet.cell(row, 16, int(closing_cents) / 100)
        sheet.cell(row, 17, f"=P{row}-L{row}-O{row}")
        sheet.cell(row, 18, f'=IFERROR(Q{row}/(L{row}+O{row}),"")')
        sheet.cell(row, 19, int(original_hwm_cents or 0) / 100)
        sheet.cell(row, 20, f"=S{row}+O{row}")
        sheet.cell(row, 21, f"=P{row}-T{row}")
        # Export the exact locked integer-cent result. Recalculating from an
        # unrounded Excel formula can differ from the finalized ledger by one cent.
        sheet.cell(row, 22, int(service_fee_cents or 0) / 100)
        sheet.cell(row, 23, f"=MAX(T{row},P{row})")
        sheet.cell(row, 24, invoice.issue_date if invoice else None)
        sheet.cell(row, 25, invoice.due_date if invoice else None)
        for col in range(2, 9):
            cell = sheet.cell(row, col)
            cell.alignment = Alignment(
                horizontal=cell.alignment.horizontal,
                vertical="center",
                wrap_text=True,
            )
        for col in range(12, 24):
            sheet.cell(row, col).number_format = '$#,##0.00;[Red]($#,##0.00);-'
        sheet.cell(row, 18).number_format = "0.00%"
        sheet.cell(row, 9).number_format = "dd/mm/yyyy"
        sheet.cell(row, 10).number_format = "dd/mm/yyyy"
        sheet.cell(row, 24).number_format = "dd/mm/yyyy"
        sheet.cell(row, 25).number_format = "dd/mm/yyyy"

    try:
        workbook.calculation.fullCalcOnLoad = True
        workbook.calculation.forceFullCalc = True
        workbook.calculation.calcMode = "auto"
    except AttributeError:
        pass
    workbook.save(output_path)
    return output_path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

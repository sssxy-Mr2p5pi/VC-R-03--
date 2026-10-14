"""Explicitly synthetic XLSX fixtures; demos use the official workbook."""
from datetime import datetime
from pathlib import Path
import sys

import pytest
from openpyxl import Workbook

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vcr03.ingest.rules import HEADERS  # noqa: E402


def line(invoice='100', stock='SKU', quantity=2, price=2.55, customer=123, country='UK'):
    return [invoice,stock,'Synthetic test fixture',quantity,datetime(2020,1,1,12),price,customer,country]


@pytest.fixture
def workbook(tmp_path):
    def make(first,second=(),name='fixture.xlsx'):
        wb=Workbook()
        wb.remove(wb.active)
        for title,rows in [('Year 2009-2010',first),('Year 2010-2011',second)]:
            ws=wb.create_sheet(title)
            ws.append(HEADERS)
            for row in rows:
                ws.append(row)
        path=tmp_path/name
        wb.save(path)
        wb.close()
        return path
    return make

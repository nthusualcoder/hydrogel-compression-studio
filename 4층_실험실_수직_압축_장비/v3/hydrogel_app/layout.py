"""창 크기 계산 (화면이 작은 노트북 대응). tkinter를 가져오지 않는 순수 계산이다."""
import ctypes
from ctypes import wintypes

DEFAULT_SIZE = (1360, 900)
MIN_SIZE = (1008, 420)   # 더 좁으면 축소 표시에서도 시험 중 응력·변형률 값이 잘린다
FRAME_W = 16    # 창 테두리 (좌우 합)
FRAME_H = 40    # 제목 표시줄 + 아래 테두리

SPI_GETWORKAREA = 48


def work_area(root):
    """작업 표시줄을 뺀 화면 영역 (left, top, right, bottom).

    exe는 DPI 인식을 선언하지 않으므로 여기서 얻는 값과 Tk가 쓰는 값은 같은 논리 좌표다.
    배율이 걸린 화면에서 Windows가 물리 좌표를 돌려주면 Tk 화면보다 커지므로 그 값은 버린다.
    """
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    try:
        rect = wintypes.RECT()
        if (ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
                and rect.right <= screen_w and rect.bottom <= screen_h):
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError):
        pass
    return 0, 0, screen_w, screen_h - 48


def fit_window(area, default=DEFAULT_SIZE, minimum=MIN_SIZE):
    """작업 영역에 들어가는 창 크기와 위치 (w, h, x, y, min_w, min_h). 창은 영역 가운데에 놓는다."""
    left, top, right, bottom = area
    area_w, area_h = right - left, bottom - top
    w = max(1, min(default[0], area_w - FRAME_W))
    h = max(1, min(default[1], area_h - FRAME_H))
    x = left + (area_w - (w + FRAME_W)) // 2
    y = top + (area_h - (h + FRAME_H)) // 2
    return w, h, x, y, min(minimum[0], w), min(minimum[1], h)

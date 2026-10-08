"""Legacy map pixel optimizer with one focused implementation owner."""

from __future__ import annotations

import copy
import logging
import math
import time
from typing import cast

import numpy as np
from numpy.typing import NDArray

from .map_javascript import optimize_map
from .map_renderer_types import ALine, Angle, CLine, Paths
from .map_types import MapData, MapImageDimensions, MapPixelType, Point

_LOGGER = logging.getLogger(__name__)


class DreameMowerMapOptimizer:
    @staticmethod
    def _valid_raster(
        map_data: MapData,
    ) -> tuple[MapImageDimensions, NDArray[np.uint8]] | None:
        """Require matching geometry before invoking either pixel algorithm."""
        dimensions, pixels = map_data.dimensions, map_data.pixel_type
        if dimensions is None or pixels is None:
            return None
        if (
            dimensions.grid_size <= 0
            or dimensions.width <= 0
            or dimensions.height <= 0
            or pixels.ndim != 2
            or pixels.shape != (dimensions.width, dimensions.height)
        ):
            return None
        return dimensions, pixels

    def _clean_wall(self, data: list[int], width: int, height: int) -> None:
        for j in range(1, height - 1):
            for i in range(1, width - 1):
                index = j * width + i
                if data[index] == 1:
                    num = 0
                    if data[index - 1] != 1:
                        num = num + 1
                    if data[index + 1] != 1:
                        num = num + 1
                    if data[index + width] != 1:
                        num = num + 1
                    if data[index - width] != 1:
                        num = num + 1
                    if num > 2:
                        data[index] = 0

        for j in range(1, height - 1):
            for i in range(1, width - 1):
                index = j * width + i
                if data[index] == 2:
                    if (data[index - 1] == 1 and data[index + 1] == 1) or (
                        data[index + width] == 1 and data[index - width] == 1
                    ):
                        data[index] = 1

        for i in range(len(data)):
            if data[i] == 2:
                data[i] = 0

    def _obstacle_data(self, data: list[int], width: int, height: int) -> None:
        for _it in range(2):
            for j in range(height):
                for i in range(width):
                    index = j * width + i
                    cValue = data[index]
                    if cValue == 2:
                        left = 0 if i == 0 else data[index - 1]
                        r = 0 if i == (width - 1) else data[index + 1]
                        t = 0 if j == (height - 1) else data[index + width]
                        b = 0 if j == 0 else data[index - width]
                        if (
                            (left == 0 and r == 2)
                            or (left == 2 and r == 0)
                            or (t == 0 and b == 2)
                            or (t == 2 and b == 0)
                        ):
                            data[index] = 0

    def _find_first_empty_point(
        self, data: list[int], width: int, height: int
    ) -> list[int] | None:
        for i in range(width):
            if data[i] == 0:
                return [i, 0]

            if data[(height - 1) * width + i] == 0:
                return [i, (height - 1)]

        for j in range(height):
            if data[j * width] == 0:
                return [0, j]

            if data[j * width + (width - 1)] == 0:
                return [(width - 1), j]
        return None

    def _find_zero_point(
        self, data: list[int], width: int, height: int, point: list[int]
    ) -> list[list[int]]:
        finds = []
        x = point[0]
        y = point[1]
        for _j in range(y - 1, y + 2):
            for _i in range(x - 1, x + 2):
                if _j == y or _i == x:
                    index = _j * width + _i
                    if data[index] == 0:
                        data[index] = 255
                        finds.append([_i, _j])
        return finds

    def _fill_map_data(
        self, data: list[int], width: int, height: int, fill: int
    ) -> None:
        self._fill_map_data_2(data, width, height)

        size = len(data)
        ssize = 3

        for _it in range(2):
            for i in range(width):
                startY = -1
                isEmpty = False
                for j in range(height):
                    index = j * width + i
                    if data[index] != 0:
                        if isEmpty and startY >= 0:
                            if (j - startY - 1) <= ssize:
                                for _j in range(startY + 1, j):
                                    num = 0
                                    if i > 0 and _j > 0:
                                        for __i in range(i - 1, i + 2):
                                            for __j in range(_j - 1, _j + 2):
                                                if __i != i and __j != _j:
                                                    if __i == i or __j == _j:
                                                        ind = __j * width + __i
                                                        if (
                                                            ind >= 0
                                                            and ind < size
                                                            and data[__j * width + __i]
                                                            != 0
                                                        ):
                                                            num = num + 1
                                    else:
                                        num = 5

                                    if num >= 3:
                                        data[_j * width + i] = fill

                            isEmpty = False
                        startY = j
                    else:
                        if startY >= 0:
                            isEmpty = True

            for j in range(height):
                startX = -1
                isEmpty = False
                for i in range(width):
                    index = j * width + i
                    if data[index] != 0:
                        if isEmpty and startX >= 0:
                            if (i - startX - 1) <= ssize:
                                for _i in range(startX + 1, i):
                                    num = 0
                                    if _i > 0 and j > 0:
                                        for __i in range(_i - 1, _i + 2):
                                            for __j in range(j - 1, j + 2):
                                                if __i != _i and __j != j:
                                                    if __i == _i or __j == j:
                                                        ind = __j * width + __i
                                                        if (
                                                            ind >= 0
                                                            and ind < size
                                                            and data[__j * width + __i]
                                                            != 0
                                                        ):
                                                            num = num + 1
                                    else:
                                        num = 5

                                    if num >= 3:
                                        data[j * width + _i] = fill

                            isEmpty = False

                        startX = i
                    else:
                        if startX >= 0:
                            isEmpty = True

    def _denoise(self, data: list[int], width: int, height: int) -> None:
        tmpMapInfo = data.copy()
        ssize = 20
        for i in range(width):
            startY = -1
            for j in range(height):
                index = j * width + i
                if data[index] != 0:
                    if startY < 0:
                        startY = j
                    continue

                if startY != -1 and (j - startY) <= ssize:
                    isBorder = False
                    if i == 0 or i == (width - 1) or (j - startY) <= 2:
                        isBorder = True

                    if not isBorder:
                        _i = i - 1
                        isBorder = True
                        for k in range(startY, j):
                            if tmpMapInfo[k * width + _i] == 1:
                                isBorder = False
                                break

                    if not isBorder:
                        _i = i + 1
                        isBorder = True
                        for k in range(startY, j):
                            if tmpMapInfo[k * width + _i] == 1:
                                isBorder = False
                                break

                    if isBorder:
                        for k in range(startY, j):
                            data[k * width + i] = 0

                startY = -1

        for j in range(height):
            startX = -1
            for i in range(width):
                index = j * width + i
                if data[index] != 0:
                    if startX < 0:
                        startX = i
                    continue

                if startX != -1 and (i - startX) <= ssize:
                    isBorder = False
                    if j == 0 or j == (height - 1) or (i - startX) <= 2:
                        isBorder = True

                    if not isBorder:
                        _j = j - 1
                        isBorder = True
                        for k in range(startX, i):
                            if tmpMapInfo[_j * width + k] == 1:
                                isBorder = False
                                break

                    if not isBorder:
                        _j = j + 1
                        isBorder = True
                        for k in range(startX, i):
                            if tmpMapInfo[_j * width + k] == 1:
                                isBorder = False
                                break

                    if isBorder:
                        for k in range(startX, i):
                            data[j * width + k] = 0

                startX = -1

        ssize = 2
        for i in range(width):
            startY = -1
            for j in range(height):
                index = j * width + i
                if data[index] != 0:
                    if startY < 0:
                        startY = j
                    continue

                if startY != -1 and (j - startY) <= ssize:
                    for k in range(startY, j):
                        data[k * width + i] = 0

                startY = -1

        for j in range(height):
            startX = -1
            for i in range(width):
                index = j * width + i
                if data[index] != 0:
                    if startX < 0:
                        startX = i
                    continue

                if startX != -1 and (i - startX) <= ssize:
                    for k in range(startX, i):
                        data[j * width + k] = 0

                startX = -1

    def _update_border_value(
        self, data: list[int], width: int, height: int, stroke: int
    ) -> None:
        for j in range(height):
            for i in range(width):
                index = j * width + i
                if data[index] != 0:
                    if j == 0 or j == (height - 1) or i == 0 or i == (width - 1):
                        data[index] = stroke
                    else:
                        hasFind = False
                        for _i in range(i - 1, i + 2):
                            for _j in range(j - 1, j + 2):
                                if data[_j * width + _i] == 0:
                                    hasFind = True
                                    break
                            if hasFind:
                                break

                        if hasFind:
                            data[index] = stroke

    def _fill_cross_line(
        self, data: list[int], width: int, height: int, stroke: int
    ) -> None:
        size = len(data)
        for i in range(width):
            startY = -1
            for j in range(height):
                index = j * width + i
                lastY = j - 1
                if data[index] == stroke and j != (height - 1):
                    if startY < 0:
                        startY = j
                    continue

                if startY >= 0:
                    if j == (height - 1) and data[index] == stroke:
                        lastY = j

                    if lastY == startY:
                        startY = -1
                        continue

                    crossNum = 0
                    for _j in range(startY, lastY + 1):
                        _i = i - 1
                        if _i >= 0:
                            cIndex = _j * width + _i
                            if cIndex < size and data[cIndex] == stroke:
                                crossNum = crossNum + 1

                        _i = i + 1
                        if _i < width:
                            cIndex = _j * width + _i
                            if cIndex < size and data[cIndex] == stroke:
                                crossNum = crossNum + 1

                        if crossNum > 2:
                            break

                    if crossNum > 2:
                        for _j in range(startY, lastY + 1):
                            _i = i - 1
                            if _i >= 0:
                                cIndex = _j * width + _i
                                if cIndex < size and data[cIndex] == 0:
                                    data[cIndex] = 1

                            _i = i + 1
                            if _i < width:
                                cIndex = _j * width + _i
                                if cIndex < size and data[cIndex] == 0:
                                    data[cIndex] = 1

                startY = -1

        for j in range(height):
            startX = -1
            for i in range(width):
                index = j * width + i
                lastX = i - 1
                if data[index] == stroke and i != (width - 1):
                    if startX < 0:
                        startX = i
                    continue

                if startX >= 0:
                    if data[index] == stroke and i == (width - 1):
                        lastX = i

                    if lastX == startX:
                        startX = -1
                        continue

                    crossNum = 0
                    for _i in range(startX, lastX + 1):
                        _j = j - 1
                        if _j >= 0:
                            cIndex = _j * width + _i
                            if cIndex < size and data[cIndex] == stroke:
                                crossNum = crossNum + 1

                        _j = j + 1
                        if _j < width:
                            cIndex = _j * width + _i
                            if cIndex < size and data[cIndex] == stroke:
                                crossNum = crossNum + 1

                        if crossNum > 2:
                            break

                    if crossNum > 2:
                        for _i in range(startX, lastX + 1):
                            _j = j - 1
                            if _j >= 0:
                                cIndex = _j * width + _i
                                if cIndex < size and data[cIndex] == 0:
                                    data[cIndex] = 1

                            _j = j + 1
                            if _j < width:
                                cIndex = _j * width + _i
                                if cIndex < size and data[cIndex] == 0:
                                    data[cIndex] = 1

                startX = -1

        for i in range(len(data)):
            if data[i] == stroke:
                data[i] = 1

        self._update_border_value(data, width, height, stroke)

    def _check_intersect(self, arr1: list[int], arr2: list[int]) -> list[int] | None:
        if arr1[0] >= arr2[1] or arr2[0] >= arr1[1]:
            return None

        tmp = sorted(arr1 + arr2)
        return [tmp[1], tmp[2]]

    def _find_original_points(
        self,
        original_data: list[int],
        data: list[int],
        width: int,
        xs: list[int],
        ys: list[int],
    ) -> float:
        if xs[0] > xs[1]:
            tmp = xs[0]
            xs[0] = xs[1]
            xs[1] = tmp

        if ys[0] > ys[1]:
            tmp = ys[0]
            ys[0] = ys[1]
            ys[1] = tmp

        num = 0
        for i in range(xs[0], xs[1] + 1):
            for j in range(ys[0], ys[1] + 1):
                value = original_data[j * width + i]
                if value != 0:
                    num = num + 1

        weight = num / ((xs[1] - xs[0] + 1) * (ys[1] - ys[0] + 1))
        if weight > 0.5:
            size = len(data)
            for i in range(xs[0], xs[1] + 1):
                for j in range(ys[0], ys[1] + 1):
                    nIndex = j * width + i
                    if nIndex < size:
                        data[nIndex] = 1
        return weight

    def _add_line(
        self, line: CLine, covertlines: list[ALine], allLines: list[CLine]
    ) -> None:
        aLine = ALine()
        if line.ishorizontal:
            aLine.p0.y = line._coordinate
            aLine.p1.y = line._coordinate
            if line.findEnd:
                aLine.p0.x = line._interval[0]
                aLine.p1.x = line._interval[1]
            else:
                aLine.p0.x = line._interval[1]
                aLine.p1.x = line._interval[0]
            aLine.length = abs(line._interval[1] - line._interval[0])
        else:
            aLine.p0.x = line._coordinate
            aLine.p1.x = line._coordinate
            aLine.length = abs(line._interval[1] - line._interval[0])
            if line.findEnd:
                aLine.p0.y = line._interval[0]
                aLine.p1.y = line._interval[1]
            else:
                aLine.p0.y = line._interval[1]
                aLine.p1.y = line._interval[0]
        covertlines.append(aLine)
        allLines.append(line)

    def _find_bounds(
        self,
        data: list[int],
        width: int,
        horizontalLines: list[CLine],
        verticalLines: list[CLine],
    ) -> list[Paths]:
        paths = []
        size = len(data)

        while horizontalLines:
            startLine = horizontalLines.pop(0)
            startLine.findEnd = True
            covertlines: list[ALine] = []
            allLines: list[CLine] = []
            self._add_line(startLine, covertlines, allLines)
            while True:
                lastLine = allLines[len(allLines) - 1]
                if lastLine.ishorizontal:
                    hasFind = False

                    lines = verticalLines.copy()
                    for i in range(len(lines)):
                        vLine = lines[i]

                        x = lastLine._interval[0]
                        if lastLine.findEnd:
                            x = lastLine._interval[1]

                        if x == vLine._coordinate:
                            if lastLine._coordinate == vLine._interval[0]:
                                vLine.findEnd = True
                                self._add_line(vLine, covertlines, allLines)
                                del verticalLines[i]
                                hasFind = True
                                break
                            elif lastLine._coordinate == vLine._interval[1]:
                                vLine.findEnd = False
                                self._add_line(vLine, covertlines, allLines)
                                del verticalLines[i]
                                hasFind = True
                                break
                            elif (
                                lastLine._coordinate > vLine._interval[0]
                                and lastLine._coordinate < vLine._interval[1]
                            ):
                                if lastLine.findEnd:
                                    nIndex = (lastLine._coordinate + 1) * width + x - 1
                                    if nIndex < size and data[nIndex] == 0:
                                        vLine._interval[1] = lastLine._coordinate
                                        vLine.findEnd = False
                                    else:
                                        vLine._interval[0] = lastLine._coordinate
                                        vLine.findEnd = True
                                else:
                                    nIndex = (lastLine._coordinate + 1) * width + x + 1
                                    if nIndex < size and data[nIndex] == 0:
                                        vLine._interval[1] = lastLine._coordinate
                                        vLine.findEnd = False
                                    else:
                                        vLine._interval[0] = lastLine._coordinate
                                        vLine.findEnd = True

                                self._add_line(vLine, covertlines, allLines)
                                del verticalLines[i]
                                hasFind = True
                                break

                    if not hasFind:
                        break
                else:
                    hasFind = False
                    _y = lastLine._interval[0]
                    if lastLine.findEnd:
                        _y = lastLine._interval[1]

                    if (
                        _y == startLine._coordinate
                        and lastLine._coordinate == startLine._interval[0]
                    ):
                        break

                    lines = horizontalLines.copy()
                    for i in range(len(lines)):
                        hLine = lines[i]

                        y = lastLine._interval[0]
                        if lastLine.findEnd:
                            y = lastLine._interval[1]

                        if y == hLine._coordinate:
                            if lastLine._coordinate == hLine._interval[0]:
                                hLine.findEnd = True
                                self._add_line(hLine, covertlines, allLines)
                                del horizontalLines[i]
                                hasFind = True
                                break
                            elif lastLine._coordinate == hLine._interval[1]:
                                hLine.findEnd = False
                                self._add_line(hLine, covertlines, allLines)
                                del horizontalLines[i]
                                hasFind = True
                                break
                            elif (
                                lastLine._coordinate > hLine._interval[0]
                                and lastLine._coordinate < hLine._interval[1]
                            ):
                                if lastLine.findEnd:
                                    nIndex = (y - 1) * width + lastLine._coordinate - 1
                                    if nIndex < size and data[nIndex] == 0:
                                        hLine._interval[0] = lastLine._coordinate
                                        hLine.findEnd = True
                                    else:
                                        hLine._interval[1] = lastLine._coordinate
                                        hLine.findEnd = False
                                else:
                                    nIndex = (y + 1) * width + lastLine._coordinate - 1
                                    if nIndex < size and data[nIndex] == 0:
                                        hLine._interval[0] = lastLine._coordinate
                                        hLine.findEnd = True
                                    else:
                                        hLine._interval[1] = lastLine._coordinate
                                        hLine.findEnd = False

                                self._add_line(hLine, covertlines, allLines)
                                del horizontalLines[i]
                                hasFind = True
                                break

                    if not hasFind:
                        break

            totalLength = 0
            for i in range(len(covertlines)):
                item = covertlines[i]
                totalLength = totalLength + item.length

            paths.append(Paths(clines=covertlines, alines=allLines, length=totalLength))

        return paths

    def _fill_map_data_2(self, data: list[int], width: int, height: int) -> None:
        while True:
            first_point = self._find_first_empty_point(data, width, height)
            if first_point is None:
                break

            data[first_point[1] * width + first_point[0]] = 255
            needFindPoints = [first_point]
            while needFindPoints:
                needFindPoints.extend(
                    self._find_zero_point(data, width, height, needFindPoints.pop(0))
                )

        for i in range(len(data)):
            if data[i] == 0:
                data[i] = 3
            elif data[i] == 255:
                data[i] = 0

    def _link_adjacent_areas(
        self,
        original_data: list[int],
        data: list[int],
        width: int,
        height: int,
        stroke: int,
    ) -> None:
        horizontalLines = []
        verticalLines = []
        DIR_LEFT = 1
        DIR_RIGHT = 2
        DIR_TOP = 3
        DIR_BOTTOM = 4
        size = len(data)
        for i in range(width):
            startY = -1
            for j in range(height):
                index = j * width + i
                lastY = j - 1
                if data[index] == stroke and j != height - 1:
                    isCross = False
                    if (i != 0 and data[index - 1] == stroke) or (
                        i != (width - 1) and data[index + 1] == stroke
                    ):
                        isCross = True
                    if startY < 0 and isCross:
                        startY = j
                        continue

                    if not isCross:
                        continue

                    lastY = j

                if startY >= 0:
                    if j == (height - 1) and data[index] == stroke:
                        lastY = j

                    if lastY == startY:
                        startY = -1
                        continue

                    isCross = False
                    direction = DIR_LEFT
                    lastIndex = lastY * width + i
                    if data[lastIndex - 1] == stroke or data[lastIndex + 1] == stroke:
                        isCross = True

                    if i == 0:
                        direction = DIR_LEFT
                    elif i == (width - 1):
                        direction = DIR_RIGHT
                    elif data[lastIndex - 1] == stroke:
                        if data[lastIndex + 1] != 0:
                            direction = DIR_LEFT
                        else:
                            direction = DIR_RIGHT
                    elif data[lastIndex + 1] == stroke:
                        if data[lastIndex - 1] != 0:
                            direction = DIR_RIGHT
                        else:
                            direction = DIR_LEFT

                    if isCross:
                        verticalLines.append(
                            CLine(
                                x=i,
                                y=[startY, lastY],
                                ishorizontal=False,
                                direction=direction,
                                length=(lastY - startY),
                            )
                        )
                        startY = lastY
                        continue
                startY = -1

        for j in range(height):
            startX = -1
            for i in range(width):
                index = j * width + i
                lastX = i - 1
                if data[index] == stroke and i != (width - 1):
                    isCross = False
                    nIndex = index - width
                    nnIndex = index + width
                    if data[nIndex] == stroke or data[nnIndex] == stroke:
                        isCross = True
                    if startX < 0 and isCross:
                        startX = i
                        continue
                    if not isCross:
                        continue

                    lastX = i

                if startX >= 0:
                    if data[index] == stroke and i == (width - 1):
                        lastX = i

                    if lastX == startX:
                        startX = -1
                        continue

                    isCross = False
                    direction = DIR_TOP
                    lastIndex = j * width + lastX
                    nIndex = lastIndex - width
                    nnIndex = lastIndex + width
                    if (nIndex >= 0 and data[nIndex] == stroke) or (
                        nnIndex < size and data[nnIndex] == stroke
                    ):
                        isCross = True

                    if j == 0:
                        direction = DIR_BOTTOM
                    elif j == (height - 1):
                        direction = DIR_TOP
                    elif data[nIndex] == stroke:
                        if data[nnIndex] != 0:
                            direction = DIR_BOTTOM
                        else:
                            direction = DIR_TOP
                    elif data[nnIndex] == stroke:
                        if data[nIndex] != 0:
                            direction = DIR_TOP
                        else:
                            direction = DIR_BOTTOM

                    if isCross:
                        horizontalLines.append(
                            CLine(
                                x=[startX, lastX],
                                y=j,
                                ishorizontal=True,
                                direction=direction,
                                length=(lastX - startX),
                            )
                        )
                        startX = lastX
                        continue

                startX = -1

        paths = self._find_bounds(data, width, horizontalLines, verticalLines)
        needFill = len(paths) > 1
        while len(paths) > 1:
            lines = paths.pop(0).alines

            for line_index in range(len(lines)):
                line = lines[line_index]
                for i in range(len(paths)):
                    nLines = paths[i].alines
                    for j in range(len(nLines)):
                        nLine = nLines[j]
                        if not line.ishorizontal and not nLine.ishorizontal:
                            if line.direction != nLine.direction:
                                if (
                                    line._coordinate > nLine._coordinate
                                    and line.direction == DIR_LEFT
                                ) or (
                                    line._coordinate < nLine._coordinate
                                    and line.direction == DIR_RIGHT
                                ):
                                    if abs(line._coordinate - nLine._coordinate) <= 10:
                                        _ys = self._check_intersect(
                                            line._interval, nLine._interval
                                        )
                                        if _ys is not None:
                                            xs = [
                                                line._coordinate + 1,
                                                nLine._coordinate - 1,
                                            ]
                                            if line._coordinate > nLine._coordinate:
                                                xs = [
                                                    nLine._coordinate + 1,
                                                    line._coordinate - 1,
                                                ]
                                            self._find_original_points(
                                                original_data, data, width, xs, _ys
                                            )
                        elif line.ishorizontal and nLine.ishorizontal:
                            if line.direction != nLine.direction:
                                if (
                                    line._coordinate > nLine._coordinate
                                    and line.direction == DIR_BOTTOM
                                ) or (
                                    line._coordinate < nLine._coordinate
                                    and line.direction == DIR_TOP
                                ):
                                    if abs(line._coordinate - nLine._coordinate) <= 10:
                                        _xs = self._check_intersect(
                                            line._interval, nLine._interval
                                        )
                                        if _xs is not None:
                                            ys = [
                                                line._coordinate + 1,
                                                nLine._coordinate - 1,
                                            ]
                                            if line._coordinate > nLine._coordinate:
                                                ys = [
                                                    nLine._coordinate + 1,
                                                    line._coordinate - 1,
                                                ]
                                            self._find_original_points(
                                                original_data, data, width, _xs, ys
                                            )

        if needFill:
            for i in range(len(data)):
                if data[i] == stroke:
                    data[i] = 1

            self._fill_map_data_2(data, width, height)
            self._update_border_value(data, width, height, stroke)
            self._fill_cross_line(data, width, height, stroke)

    def _fill_angle(
        self, data: list[int], width: int, stroke: int, angle: Angle
    ) -> Angle:
        bottom = 5
        right = 6
        top = 7
        left = 8

        l1 = angle.lines[0]
        l2 = angle.lines[len(angle.lines) - 1]
        if len(angle.lines) == 2 or len(angle.lines) > 22:
            nextAngle = Angle(lines=[l2])
            if l2.ishorizontal:
                nextAngle.horizontalDir = right if l2.findEnd else left
            else:
                nextAngle.verticalDir = top if l2.findEnd else bottom
            return nextAngle

        minx = None
        miny = None
        maxx = None
        maxy = None
        if l1.ishorizontal:
            if angle.horizontalDir == right:
                minx = l1._interval[1]
            else:
                maxx = l1._interval[0]

            if angle.verticalDir == top:
                miny = l1._coordinate
            else:
                maxy = l1._coordinate

            if l2.ishorizontal:
                if angle.horizontalDir == right:
                    maxx = l2._interval[0]
                else:
                    minx = l2._interval[1]

                if angle.verticalDir == top:
                    maxy = l2._coordinate
                else:
                    miny = l2._coordinate
            else:
                if angle.horizontalDir == right:
                    maxx = l2._coordinate
                else:
                    minx = l2._coordinate
                if angle.verticalDir == top:
                    maxy = l2._interval[0]
                else:
                    miny = l2._interval[1]
        else:
            if angle.verticalDir == top:
                miny = l1._interval[1]
            else:
                maxy = l1._interval[0]

            if angle.horizontalDir == right:
                minx = l1._coordinate
            else:
                maxx = l1._coordinate

            if l2.ishorizontal:
                if angle.horizontalDir == right:
                    maxx = l2._interval[0]
                else:
                    minx = l2._interval[1]
                if angle.verticalDir == top:
                    maxy = l2._coordinate
                else:
                    miny = l2._coordinate

            else:
                if angle.horizontalDir == right:
                    maxx = l2._coordinate
                else:
                    minx = l2._coordinate

                if angle.verticalDir == top:
                    maxy = l2._interval[0]
                else:
                    miny = l2._interval[1]

        if minx is None or miny is None or maxx is None or maxy is None:
            nextAngle = Angle(lines=[l2])
            if l2.ishorizontal:
                nextAngle.horizontalDir = right if l2.findEnd else left
            else:
                nextAngle.verticalDir = top if l2.findEnd else bottom
            return nextAngle

        if l1.ishorizontal and l2.ishorizontal and ((maxy - miny) <= 3):
            if angle.horizontalDir == right:
                minx = l1._interval[0]
                maxx = l2._interval[1]
            else:
                minx = l2._interval[0]
                maxx = l1._interval[1]
        elif not l1.ishorizontal and not l2.ishorizontal and ((maxx - minx) <= 3):
            if angle.verticalDir == top:
                miny = l1._interval[0]
                maxy = l2._interval[1]
            else:
                miny = l2._interval[0]
                maxy = l1._interval[1]

        num = 0
        for i in range(minx, maxx + 1):
            for j in range(miny, maxy + 1):
                index = j * width + i
                if data[index] == 0:
                    num = num + 1

        if num < 20 or num < (((maxx - minx + 1) * (maxy - miny + 1) * 2) / 3):
            for i in range(minx, maxx + 1):
                for j in range(miny, maxy + 1):
                    index = j * width + i
                    if index < len(data) and data[index] == 0:
                        data[index] = stroke

        nextAngle = Angle(lines=[l2])
        if l2.ishorizontal:
            nextAngle.horizontalDir = right if l2.findEnd else left
        else:
            nextAngle.verticalDir = top if l2.findEnd else bottom
        return nextAngle

    def _find_outline(
        self, data: list[int], width: int, height: int, stroke: int, first: bool
    ) -> bool:
        horizontalLines = []
        verticalLines = []
        size = len(data)

        for i in range(width):
            startY = -1
            for j in range(height):
                index = j * width + i
                lastY = j - 1
                if data[index] == stroke and j != (height - 1):
                    isCross = False
                    if (i != 0 and data[index - 1] == stroke) or (
                        i != (width - 1) and data[index + 1] == stroke
                    ):
                        isCross = True
                    if startY < 0 and isCross:
                        startY = j
                        continue
                    if not isCross:
                        continue
                    lastY = j

                if startY >= 0:
                    if j == (height - 1) and data[index] == stroke:
                        lastY = j
                    if lastY == startY:
                        startY = -1
                        continue
                    isCross = False
                    lastIndex = lastY * width + i
                    if data[lastIndex - 1] == stroke or data[lastIndex + 1] == stroke:
                        isCross = True

                    if isCross:
                        verticalLines.append(
                            CLine(
                                x=i,
                                y=[startY, lastY],
                                ishorizontal=False,
                                length=(lastY - startY),
                            )
                        )
                        startY = lastY
                        continue
                startY = -1

        for j in range(height):
            startX = -1
            for i in range(width):
                index = j * width + i
                lastX = i - 1
                if data[index] == stroke and i != (width - 1):
                    isCross = False
                    if data[index - width] == stroke or data[index + width] == stroke:
                        isCross = True
                    if startX < 0 and isCross:
                        startX = i
                        continue
                    if not isCross:
                        continue

                    lastX = i

                if startX >= 0:
                    if data[index] == stroke and i == (width - 1):
                        lastX = i

                    if lastX == startX:
                        startX = -1
                        continue
                    isCross = False
                    nIndex = lastIndex - width
                    nnIndex = lastIndex + width
                    if (nIndex >= 0 and data[nIndex] == stroke) or (
                        nnIndex < size and data[nnIndex] == stroke
                    ):
                        isCross = True

                    if isCross:
                        horizontalLines.append(
                            CLine(
                                x=[startX, lastX],
                                y=j,
                                ishorizontal=True,
                                length=(lastX - startX),
                            )
                        )
                        startX = lastX
                        continue
                startX = -1

        if not horizontalLines:
            return False

        paths = self._find_bounds(data, width, horizontalLines, verticalLines)

        covertlines = None
        allLines = None
        totalLen = 0
        tmp = []
        for i in range(len(paths)):
            item = paths[i]
            plen = item.length
            if plen > totalLen:
                if covertlines and totalLen < 80:
                    tmp.append(covertlines)
                totalLen = plen
                covertlines = item.clines
                allLines = item.alines
            else:
                if plen < 80:
                    tmp.append(item.clines)

        if first and tmp:
            clearPos = []
            for i in range(len(tmp)):
                point_x, point_y = tmp[i][0].p0.x, tmp[i][0].p0.y
                if not isinstance(point_x, int) or not isinstance(point_y, int):
                    raise ValueError("Outline endpoint coordinates must be integers.")
                clearPos.append([point_x, point_y])

            while clearPos:
                pos = clearPos.pop()
                x = pos[0]
                y = pos[1]
                data[y * width + x] = 0
                for _i in range(x - 1, x + 2):
                    for _j in range(y - 1, y + 2):
                        if _i == x or _j == y:
                            index = (_j * width) + _i
                            if index < len(data) and data[index] != 0:
                                clearPos.append([_i, _j])

        bottom = 5
        right = 6
        top = 7
        left = 8
        dirnone = 0

        if not allLines:
            return False

        angle = Angle()
        for i in range(len(allLines) + 1):
            line = allLines[0 if i == len(allLines) else i]

            if i == 0:
                angle.lines.append(line)
                angle.horizontalDir = right
            else:
                if line.ishorizontal:
                    horizontalDir = right if line.findEnd else left
                    if (
                        angle.horizontalDir != dirnone
                        and angle.horizontalDir != horizontalDir
                    ):
                        angle = self._fill_angle(data, width, stroke, angle)

                    if angle.horizontalDir == dirnone:
                        angle.horizontalDir = horizontalDir
                    angle.lines.append(line)
                else:
                    verticalDir = top if line.findEnd else bottom
                    if (
                        angle.verticalDir != dirnone
                        and angle.verticalDir != verticalDir
                    ):
                        angle = self._fill_angle(data, width, stroke, angle)
                    if angle.verticalDir == dirnone:
                        angle.verticalDir = verticalDir
                    angle.lines.append(line)

                if line.length >= 7 or i == len(allLines):
                    angle = self._fill_angle(data, width, stroke, angle)

        return True

    def _find_obstacle_border(
        self, data: list[int], width: int, height: int, stroke: int
    ) -> None:
        size = len(data)
        for j in range(height):
            for i in range(width):
                index = j * width + i
                if data[index] == stroke:
                    if j == 0 or j == (height - 1) or i == 0 or i == (width - 1):
                        data[index] = 2
                        continue
                    hasFind = False
                    for _i in range(i - 1, i + 2):
                        for _j in range(j - 1, j + 2):
                            nIndex = _j * width + _i
                            if (
                                nIndex < size
                                and data[nIndex] != stroke
                                and data[nIndex] != 2
                            ):
                                hasFind = True
                                break
                        if hasFind:
                            break

                    if hasFind:
                        data[index] = 2

    def _clean_small_obstacle(
        self, data: list[int], width: int, height: int, stroke: int
    ) -> None:
        for i in range(width):
            startY = -1
            for j in range(height):
                index = j * width + i
                if data[index] == stroke:
                    if startY < 0:
                        startY = j
                    continue
                if startY != -1 and (j - startY) <= 3:
                    for k in range(startY, j):
                        data[k * width + i] = 1
                startY = -1
        for j in range(height):
            startX = -1
            for i in range(width):
                index = j * width + i
                if data[index] == stroke:
                    if startX < 0:
                        startX = i
                    continue
                if startX != -1 and (i - startX) <= 3:
                    for k in range(startX, i):
                        data[j * width + k] = 1
                startX = -1

    def _calculate_charger_position(
        self,
        data: list[int],
        width: int,
        height: int,
        stroke: int,
        charger_position: Point,
    ) -> Point:
        heading = charger_position.a
        if heading is None:
            return charger_position
        lastX: int | None
        lastY: int | None
        vLines = []
        hLines = []
        for i in range(width):
            startY = -1
            for j in range(height):
                index = j * width + i
                lastY = j - 1
                if data[index] == stroke and j != (height - 1):
                    isCross = False
                    if (i != 0 and data[index - 1] == stroke) or (
                        i != width - 1 and data[index + 1] == stroke
                    ):
                        isCross = True
                    if startY < 0 and isCross:
                        startY = j
                        continue
                    if not isCross:
                        continue
                    lastY = j
                if startY >= 0:
                    if j == height - 1 and data[index] == stroke:
                        lastY = j
                    if lastY == startY:
                        startY = -1
                        continue
                    isCross = False
                    lastIndex = lastY * width + i
                    if data[lastIndex - 1] == stroke or data[lastIndex + 1] == stroke:
                        isCross = True

                    if isCross:
                        vLines.append([[i, startY], [i, lastY]])
                        startY = lastY
                        continue
                startY = -1

        for j in range(height):
            startX = -1
            for i in range(width):
                index = j * width + i
                lastX = i - 1
                if data[index] == stroke and i != width - 1:
                    isCross = False
                    if data[index - width] == stroke or data[index + width] == stroke:
                        isCross = True
                    if startX < 0 and isCross:
                        startX = i
                        continue
                    if not isCross:
                        continue
                    lastX = i
                if startX >= 0:
                    if data[index] == stroke and i == width - 1:
                        lastX = i

                    if lastX == startX:
                        startX = -1
                        continue
                    isCross = False
                    lastIndex = j * width + lastX
                    if (
                        data[lastIndex - width] == stroke
                        or data[lastIndex + width] == stroke
                    ):
                        isCross = True

                    if isCross:
                        hLines.append([[startX, j], [lastX, j]])

                        startX = lastX
                        continue
                startX = -1

        cX = math.floor(charger_position.x)
        cY = math.floor(charger_position.y)
        if abs(heading - 180) <= 30:
            charger_position.a = 180
            lastX = None
            for i in range(len(vLines)):
                line = vLines[i]
                lx = line[0][0]
                minY = line[0][1] if line[0][1] < line[1][1] else line[1][1]
                maxY = line[0][1] if line[0][1] > line[1][1] else line[1][1]
                if lx >= cX and cY >= minY and cY <= maxY:
                    if lastX is None or lx < lastX:
                        lastX = lx
            if lastX is not None:
                if lastX - cX <= 11:
                    charger_position.a = 180
                    charger_position.x = lastX + 0.5
        elif abs(heading - 360) <= 30 or abs(heading) <= 3:
            charger_position.a = 360
            lastX = None
            for i in range(len(vLines)):
                line = vLines[i]
                lx = line[0][0]
                minY = line[0][1] if line[0][1] < line[1][1] else line[1][1]
                maxY = line[0][1] if line[0][1] > line[1][1] else line[1][1]
                if lx <= cX and cY >= minY and cY <= maxY:
                    if lastX is None or lx > lastX:
                        lastX = lx
            if lastX is not None:
                if cX - lastX <= 11:
                    charger_position.a = 360
                    charger_position.x = lastX + 0.5
        elif abs(abs(heading - 270) <= 30):
            lastY = None
            for i in range(len(hLines)):
                line = hLines[i]
                ly = line[0][1]
                minX = line[0][0] if line[0][0] < line[1][0] else line[1][0]
                maxX = line[0][0] if line[0][0] > line[1][0] else line[1][0]
                if ly >= cY and cX >= minX and cX <= maxX:
                    if lastY is None or ly < lastY:
                        lastY = ly
            if lastY is not None:
                if lastY - cY <= 11:
                    charger_position.a = 270
                    charger_position.y = lastY + 0.5
        elif abs(abs(heading - 90) <= 30):
            lastY = None
            for i in range(len(hLines)):
                line = hLines[i]
                ly = line[0][1]
                minX = line[0][0] if line[0][0] < line[1][0] else line[1][0]
                maxX = line[0][0] if line[0][0] > line[1][0] else line[1][0]
                if ly <= cY and cX >= minX and cX <= maxX:
                    if lastY is None or ly > lastY:
                        lastY = ly
            if lastY is not None:
                if cY - lastY <= 11:
                    charger_position.a = 90
                    charger_position.y = lastY + 0.5

        return charger_position

    def _merge_saved_map_data(
        self,
        map_data: MapData,
        saved_map_data: MapData | None,
        original_data: list[int] | None = None,
    ) -> None:
        if saved_map_data is None:
            return
        current_geometry = self._valid_raster(map_data)
        saved_geometry = self._valid_raster(saved_map_data)
        if current_geometry is None or saved_geometry is None:
            return
        dimensions, source_pixels = current_geometry
        saved_dimensions, saved_pixels = saved_geometry
        if saved_dimensions.grid_size != dimensions.grid_size:
            return
        if saved_map_data:
            maxX = dimensions.left + (dimensions.width * dimensions.grid_size)
            maxY = dimensions.top + (dimensions.height * dimensions.grid_size)

            if maxX < saved_dimensions.left + (
                saved_dimensions.width * saved_dimensions.grid_size
            ):
                maxX = saved_dimensions.left + (
                    saved_dimensions.width * saved_dimensions.grid_size
                )

            if maxY < saved_dimensions.top + (
                saved_dimensions.height * saved_dimensions.grid_size
            ):
                maxY = saved_dimensions.top + (
                    saved_dimensions.height * saved_dimensions.grid_size
                )

            left = dimensions.left
            top = dimensions.top

            if saved_dimensions.left < left:
                left = saved_dimensions.left

            if saved_dimensions.top < top:
                top = saved_dimensions.top

            width = int((maxX - left) / saved_dimensions.grid_size)
            height = int((maxY - top) / saved_dimensions.grid_size)

            si = int((saved_dimensions.left - left) / saved_dimensions.grid_size)
            sj = int((saved_dimensions.top - top) / saved_dimensions.grid_size)

            sim = si + saved_dimensions.width
            sjm = sj + saved_dimensions.height

            ni = int((dimensions.left - left) / dimensions.grid_size)
            nj = int((dimensions.top - top) / dimensions.grid_size)

            nim = ni + dimensions.width
            njm = nj + dimensions.height

            pixel_type = np.zeros((width, height), np.uint8)
            data = (
                map_data.optimized_pixel_type
                if map_data.optimized_pixel_type is not None
                else source_pixels
            )

            for j in range(height):
                for i in range(width):
                    if j >= sj and i >= si and j < sjm and i < sim:
                        saved_value = int(saved_pixels[(i - si), (j - sj)])
                    else:
                        saved_value = 0

                    if j >= nj and i >= ni and j < njm and i < nim:
                        clean_value = int(data[(i - ni), (j - nj)])
                    else:
                        clean_value = 0

                    if saved_value != 0:
                        if saved_value != 255:
                            pixel_type[i, j] = saved_value
                        else:
                            if clean_value != 0 and clean_value != 255:
                                pixel_type[i, j] = 254
                            else:
                                pixel_type[i, j] = 255
                    elif clean_value != 0:
                        if clean_value == 255:
                            pixel_type[i, j] = 255
                        else:
                            pixel_type[i, j] = 254

            if original_data is not None:
                for j in range(height):
                    for i in range(width):
                        if j >= nj and i >= ni and j < njm and i < nim:
                            if (
                                original_data[(j - nj) * dimensions.width + (i - ni)]
                                == 2
                                and pixel_type[i, j] != 0
                            ):
                                dis = 3
                                hasBorder = False
                                for _j in range(j - dis, j + dis + 1):
                                    for _i in range(i - dis, i + dis):
                                        if (
                                            _j < 0
                                            or _i < 0
                                            or _j >= height
                                            or _i >= width
                                        ):
                                            continue
                                        if hasBorder:
                                            break
                                        if pixel_type[_i, _j] == 255:
                                            hasBorder = True
                                            break

                                if not hasBorder:
                                    pixel_type[i, j] = 251

            map_data.optimized_pixel_type = pixel_type
            map_data.optimized_dimensions = MapImageDimensions(
                top, left, height, width, dimensions.grid_size
            )

    def optimize(
        self,
        map_data: MapData,
        saved_map_data: MapData | None = None,
        js_optimizer: bool = True,
    ) -> MapData:
        if map_data.saved_map:
            return map_data

        current_geometry = self._valid_raster(map_data)
        if current_geometry is None:
            return map_data
        dimensions, source_pixels = current_geometry
        saved_geometry = (
            self._valid_raster(saved_map_data) if saved_map_data is not None else None
        )
        if (
            saved_geometry is not None
            and saved_geometry[0].grid_size != dimensions.grid_size
        ):
            saved_geometry = None
        if saved_geometry is None:
            saved_map_data = None
        else:
            saved_dimensions, saved_pixels = saved_geometry

        if map_data.wifi_map:
            map_data.optimized_pixel_type = np.copy(source_pixels)
            map_data.optimized_dimensions = dimensions
            if not map_data.empty_map:
                for y in range(dimensions.height):
                    for x in range(dimensions.width):
                        if int(source_pixels[x, y]) > 2:
                            max_count = 0
                            max_px = -1
                            value_count = [0, 0, 0, 0]
                            for delta in range(3, 6):
                                for n in range(y - delta, y + delta + 1):
                                    for m in range(x - delta, x + delta + 1):
                                        if (
                                            n < 0
                                            or n >= dimensions.height
                                            or m < 0
                                            or m >= dimensions.width
                                        ):
                                            continue

                                        px = int(source_pixels[m, n]) - 11
                                        if 0 <= px < len(value_count):
                                            value_count[px] = value_count[px] + 1
                                            if value_count[px] > max_count:
                                                max_count = value_count[px]
                                                max_px = px

                                if max_px >= 0:
                                    map_data.optimized_pixel_type[x, y] = MapPixelType(
                                        max_px + 11
                                    )
                                    break
            return map_data

        try:
            now = time.time()

            if js_optimizer:
                data = cast(list[list[int]], source_pixels.tolist())
                data_size = [
                    dimensions.left,
                    dimensions.top,
                    dimensions.width,
                    dimensions.height,
                    dimensions.grid_size,
                ]
                saved_data = (
                    cast(list[list[int]], saved_pixels.tolist())
                    if saved_geometry is not None
                    else None
                )
                saved_data_size = (
                    [
                        saved_dimensions.left,
                        saved_dimensions.top,
                        saved_dimensions.width,
                        saved_dimensions.height,
                        saved_dimensions.grid_size,
                    ]
                    if saved_map_data
                    else None
                )
                charger_position = None
                if map_data.charger_position:
                    left = dimensions.left
                    top = dimensions.top

                    if saved_geometry is not None:
                        if saved_dimensions.left < left:
                            left = saved_dimensions.left

                        if saved_dimensions.top < top:
                            top = saved_dimensions.top

                    charger_position = [
                        (map_data.charger_position.x - left) / dimensions.grid_size,
                        (map_data.charger_position.y - top) / dimensions.grid_size,
                        map_data.charger_position.a,
                    ]

                result = optimize_map(
                    data,
                    data_size,
                    saved_data,
                    saved_data_size,
                    charger_position,
                )
                if result and result[0]:
                    map_data.optimized_pixel_type = np.array(result[0], dtype=np.uint8)

                    result_dimensions = result[1]
                    map_data.optimized_dimensions = MapImageDimensions(
                        result_dimensions[1],
                        result_dimensions[0],
                        result_dimensions[3],
                        result_dimensions[2],
                        dimensions.grid_size,
                    )
            else:
                width = dimensions.width
                height = dimensions.height
                clean_data = np.zeros((width * height), np.uint8).tolist()

                data_map = {255: 2, 253: 1, 250: 3}
                pointNum = 0
                for j in range(height):
                    for i in range(width):
                        index = j * width + i
                        clean_data[index] = int(source_pixels[i, j])
                        if clean_data[index]:
                            pointNum = pointNum + 1
                            clean_data[index] = data_map.get(clean_data[index], 0)

                original_data = clean_data.copy()
                pixel_type = np.zeros((width, height), np.uint8)

                self._clean_wall(clean_data, width, height)
                self._fill_map_data(clean_data, width, height, 3)
                self._denoise(clean_data, width, height)
                self._update_border_value(clean_data, width, height, 5)
                self._fill_cross_line(clean_data, width, height, 5)
                self._link_adjacent_areas(original_data, clean_data, width, height, 5)

                result = self._find_outline(clean_data, width, height, 5, True)
                if result:
                    self._fill_map_data_2(clean_data, width, height)
                    self._update_border_value(clean_data, width, height, 6)
                    if (
                        map_data.charger_position
                        and map_data.charger_position.a is not None
                    ):
                        left = dimensions.left
                        top = dimensions.top

                        if saved_geometry is not None:
                            if saved_dimensions.left < left:
                                left = saved_dimensions.left

                            if saved_dimensions.top < top:
                                top = saved_dimensions.top

                        new_charger_position = copy.deepcopy(map_data.charger_position)
                        new_charger_position.x = int(
                            (new_charger_position.x - left) / dimensions.grid_size
                        )
                        new_charger_position.y = int(
                            (new_charger_position.y - top) / dimensions.grid_size
                        )
                        if (
                            new_charger_position.y >= 0
                            and new_charger_position.x >= 0
                            and new_charger_position.y < height
                            and new_charger_position.x < width
                            and clean_data[
                                int(math.floor(new_charger_position.y)) * width
                                + int(math.floor(new_charger_position.x))
                            ]
                        ):
                            new_charger_position = self._calculate_charger_position(
                                clean_data, width, height, 6, new_charger_position
                            )
                            map_data.optimized_charger_position = Point(
                                int(new_charger_position.x * dimensions.grid_size)
                                + left,
                                int(new_charger_position.y * dimensions.grid_size)
                                + top,
                                new_charger_position.a,
                            )

                    self._find_outline(clean_data, width, height, 6, False)
                    self._fill_map_data_2(clean_data, width, height)
                    self._update_border_value(clean_data, width, height, 7)

                    if saved_geometry is not None:
                        self._find_obstacle_border(clean_data, width, height, 3)
                        self._obstacle_data(original_data, width, height)
                    else:
                        self._clean_small_obstacle(clean_data, width, height, 3)

                    currentPointNum = 0
                    data_map = {7: 255, 2: 255, 3: (0 if saved_map_data else 250)}
                    for j in range(height):
                        for i in range(width):
                            clean_value = clean_data[j * width + i]
                            if clean_value != 0:
                                currentPointNum = currentPointNum + 1
                                pixel_type[i, j] = data_map.get(clean_value, 253)

                    if (
                        not ((currentPointNum * 100) / pointNum) < 50
                        and pointNum > 2000
                    ):
                        map_data.optimized_pixel_type = pixel_type
                        map_data.optimized_dimensions = dimensions

                self._merge_saved_map_data(map_data, saved_map_data, original_data)

            _LOGGER.info(
                "Optimize Map Data: %s:%s took: %.2f",
                map_data.map_id,
                map_data.frame_id,
                time.time() - now,
            )
        except Exception as ex:
            _LOGGER.warning("Optimize map failed: %s", ex)

            self._merge_saved_map_data(map_data, saved_map_data)

        return map_data

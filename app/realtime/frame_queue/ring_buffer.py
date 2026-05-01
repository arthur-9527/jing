"""
环形缓冲区 - 移植自前端 RingBuffer.ts

固定大小，自动覆盖最旧数据，用于流式帧管理。
"""

from typing import TypeVar, Generic, Optional

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """环形缓冲区"""

    def __init__(self, size: int):
        if size <= 0:
            raise ValueError("RingBuffer size must be greater than 0")
        self._size = size
        self._buffer: list[Optional[T]] = [None] * size
        self._write_index = 0
        self._read_index = 0
        self._count = 0

    # === 基本读写 ===

    def write(self, data: T) -> None:
        """写入一帧（缓冲区满时覆盖最旧数据）"""
        self._buffer[self._write_index] = data
        self._write_index = (self._write_index + 1) % self._size

        if self._count < self._size:
            self._count += 1
        else:
            # 满了，读索引跟着前进
            self._read_index = (self._read_index + 1) % self._size

    def read(self) -> Optional[T]:
        """读取一帧（消费，移动读索引）"""
        if self._count == 0:
            return None

        data = self._buffer[self._read_index]
        self._buffer[self._read_index] = None
        self._read_index = (self._read_index + 1) % self._size
        self._count -= 1
        return data

    def read_batch(self, n: int) -> list[T]:
        """批量读取最多 n 帧"""
        result = []
        for _ in range(min(n, self._count)):
            frame = self.read()
            if frame is not None:
                result.append(frame)
        return result

    def write_batch(self, items: list[T]) -> None:
        """批量写入"""
        for item in items:
            self.write(item)

    # === 查看（不消费） ===

    def peek(self) -> Optional[T]:
        """查看下一个要读的帧"""
        if self._count == 0:
            return None
        return self._buffer[self._read_index]

    def peek_last(self) -> Optional[T]:
        """查看最后写入的帧"""
        if self._count == 0:
            return None
        last_index = (self._write_index - 1 + self._size) % self._size
        return self._buffer[last_index]

    def peek_from_end(self, offset: int) -> Optional[T]:
        """从末尾向前偏移查看（0=最后一个, 1=倒数第二个）"""
        if offset < 0 or offset >= self._count:
            return None
        index = (self._write_index - 1 - offset + self._size) % self._size
        return self._buffer[index]

    def peek_from_start(self, offset: int) -> Optional[T]:
        """从队首向后偏移查看（0=第一个, 1=第二个）"""
        if offset < 0 or offset >= self._count:
            return None
        index = (self._read_index + offset) % self._size
        return self._buffer[index]

    # === 插入/截断 ===

    def insert_from_end(self, offset_from_end: int, items: list[T]) -> None:
        """
        从末尾向前截断，然后写入新数据。
        offset_from_end=15 表示丢弃最后 15 帧，然后追加 items。
        """
        offset_from_end = max(0, min(offset_from_end, self._count))

        # 回退 write_index
        self._write_index = (self._write_index - offset_from_end + self._size) % self._size
        self._count -= offset_from_end

        # 写入新数据
        for item in items:
            self.write(item)

    def replace_after_prefix(self, prefix_count: int, items: list[T]) -> None:
        """
        保留队首 prefix_count 帧，其后全部替换为 items。

        prefix_count=5 表示保留前 5 帧，丢弃其后所有帧，然后追加 items。
        """
        prefix_count = max(0, min(prefix_count, self._count))

        # 将写指针移动到 prefix_count 之后
        self._write_index = (self._read_index + prefix_count) % self._size
        self._count = prefix_count

        # 写入新数据
        for item in items:
            self.write(item)

    # === 清空 ===

    def clear(self) -> None:
        """清空缓冲区"""
        self._buffer = [None] * self._size
        self._write_index = 0
        self._read_index = 0
        self._count = 0

    # === 属性 ===

    @property
    def count(self) -> int:
        return self._count

    @property
    def size(self) -> int:
        return self._size

    @property
    def is_empty(self) -> bool:
        return self._count == 0

    @property
    def is_full(self) -> bool:
        return self._count == self._size

    @property
    def usage(self) -> float:
        return self._count / self._size if self._size > 0 else 0.0

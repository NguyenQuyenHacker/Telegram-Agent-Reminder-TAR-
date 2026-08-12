from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class GroupCode(SQLModel, table=True):
    """Bộ đếm mã việc của một nhóm. Mỗi nhóm giữ một tiền tố và số thứ tự kế tiếp."""

    __tablename__ = "group_code"

    # "group" là từ khoá SQL nên phải khai báo tên cột tường minh
    group: str = Field(sa_column=Column("group", Text, primary_key=True))
    prefix: str = Field(unique=True)
    next_seq: int = 1

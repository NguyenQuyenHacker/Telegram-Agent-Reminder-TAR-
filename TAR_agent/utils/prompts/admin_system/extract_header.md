Bạn đọc dòng tiêu đề của một bảng Excel và cho biết mỗi trường dữ liệu nằm ở
CỘT NÀO.

Bạn KHÔNG đọc dữ liệu. Bạn chỉ ánh xạ tên cột. Vài dòng đầu của bảng đưa kèm
chỉ để bạn nhìn thấy nội dung thật của từng cột mà đoán cho đúng.

## Các trường cần tìm cột

| Trường | Cột trong file thường tên là |
|---|---|
| `tt` | "TT", "STT", "Số TT" — cột đánh số mục, chứa `I`, `I.1`, `I.2.1`, hoặc `-` |
| `cong_viec` | "Hạng mục thực hiện", "Nội dung công việc", "Tên công việc" |
| `don_vi` | "Đơn vị thực hiện", "Đơn vị chủ trì", "Cơ quan thực hiện" |
| `ngay_bd` | "Ngày bắt đầu", "Thời gian bắt đầu", "Từ ngày" |
| `ngay_ht` | "Ngày hoàn thành", "Thời gian kết thúc", "Đến ngày" |
| `so_ngay` | "Số ngày", "Thời gian (ngày)", "Số ngày thực hiện" |
| `can_cu_phap_ly` | "Căn cứ pháp lý", "Cơ sở pháp lý" |
| `ket_qua_dau_ra` | "Kết quả đầu ra", "Sản phẩm", "Kết quả" |
| `ghi_chu` | "Ghi chú", "Chú thích" |

## Quy tắc

1. Giá trị bạn trả về là **tên cột chép nguyên văn** từ dòng tiêu đề — đúng
   từng chữ, đúng dấu, không sửa hoa thường, không rút gọn. Đó là thứ dùng để
   dò lại cột, viết khác đi một chữ là không dò ra.
2. Bảng không có cột nào cho một trường thì để `null`. **Đừng gán bừa một cột
   gần giống.** Thiếu một cột chỉ mất một trường; gán nhầm thì mọi dòng của cả
   sheet mang dữ liệu sai, và không ai đọc lại để phát hiện.
3. Một cột chỉ được gán cho MỘT trường.
4. Bảng không phải bảng lịch công việc (danh sách vướng mắc, bảng ký nhận, sheet
   ghi chú...) thì để **tất cả** các trường `null`.

## Dòng tiêu đề

{{HEADER}}

## Vài dòng dữ liệu đầu tiên

{{SAMPLE}}

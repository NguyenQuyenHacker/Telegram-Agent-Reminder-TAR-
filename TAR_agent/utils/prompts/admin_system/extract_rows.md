Bạn rút các dòng công việc có cấu trúc ra khỏi một đoạn văn bản tiến độ dự án.

Mốc dữ liệu của tài liệu này: {{AS_OF}}.

## Đầu ra

Một danh sách `rows`. Mỗi phần tử là MỘT công việc, với các trường:

- `cong_viec` — tên hạng mục / việc phải làm. **BẮT BUỘC.** Không có thì không
  phải một dòng công việc, đừng tạo ra nó.
- `giai_doan`, `nhom`, `nhom_con` — cấp phân loại mà việc này nằm dưới, suy từ
  các tiêu đề phía trên nó trong văn bản. Văn bản không phân cấp đến mức đó thì
  để trống.
- `don_vi` — đơn vị thực hiện.
- `ngay_bd`, `ngay_ht` — ngày bắt đầu và ngày hoàn thành, dạng `YYYY-MM-DD`.
- `can_cu_phap_ly`, `ket_qua_dau_ra`, `ghi_chu` — xem luật 3.

## Luật

**1. Chỉ rút thứ CÓ TRONG văn bản.** Không suy, không nội suy, không lấy giá
trị của dòng gần nhất để lấp chỗ trống. Ô nào văn bản không nói thì để trống.

**2. Ngày phải xuất hiện NGUYÊN VĂN trong văn bản.** Đây là chỗ dễ sai nhất:
một ngày bịa ra luôn trông hợp lý. Văn bản viết "cuối quý II" thì để trống, đừng
đổi thành `2026-06-30`. Văn bản viết "30/9" mà không có năm thì lấy năm của mốc
dữ liệu ở trên. Có một lớp kiểm đối chiếu từng ngày với văn bản gốc và **loại
cả dòng** nếu không khớp — một ngày đoán ra làm mất luôn phần dữ liệu đúng của
dòng đó.

**3. `can_cu_phap_ly`, `ket_qua_dau_ra`, `ghi_chu`: CHÉP NGUYÊN VĂN.**
- Ô trống thì để trống. Đừng viết "chưa có", "không có", "N/A".
- KHÔNG tóm tắt, KHÔNG diễn giải, KHÔNG dịch sang thuật ngữ khác.
- KHÔNG suy ra tiến độ hay trạng thái từ chúng. "Dự kiến xin cấp GPMT do yếu tố
  đặc thù" phải được chép đúng như vậy — không đổi thành "đang chậm", không đổi
  thành "chưa hoàn thành". Đây là chú thích của người viết file, không phải một
  ô trạng thái, và nó được lưu để người đọc tự hiểu.

**4. KHÔNG tính `số ngày`** — không có trường đó, và đã có chỗ khác tính từ hai
mốc. Đừng nhét nó vào `ghi_chu`.

**5. KHÔNG có trường tên dự án.** Dự án đã biết trước khi đọc file.

**6. Đoạn không chứa dòng công việc nào** (lời mở đầu, danh sách vướng mắc,
biên bản họp) thì trả `rows` rỗng. Danh sách rỗng là một câu trả lời đúng.

# Spesifikasi Teknis: Gyro Racing Wheel Android untuk PC Windows

## 1. Ringkasan Proyek & Batasan (Scope)
Bangun sistem setir dan pedal virtual berlatensi rendah yang mengubah HP Android menjadi *controller* balap untuk PC Windows (seperti *Assetto Corsa*, *Forza Horizon*, dan *Euro Truck Simulator 2*). Sistem bekerja dengan mengirimkan data sensor mentah dan sentuhan pedal dari browser HP melalui WebSocket ke server Python di PC, lalu meneruskannya ke *controller* virtual.

### Keputusan Scope yang Dikunci:
* **Target Klien:** Khusus Android menggunakan browser Chrome (memanfaatkan Generic Sensor API, Fullscreen API, *orientation lock*, dan Wake Lock).
* **Layar Start Wajib:** Sediakan layar *Start* awal karena fitur Fullscreen dan Wake Lock mewajibkan adanya interaksi/gestur dari pengguna terlebih dahulu.
* **Pedal Layar Penuh:** Layar dibagi menjadi dua zona sentuh penuh, yaitu setengah kiri untuk rem (*brake*) dan setengah kanan untuk gas (*throttle*).
* **Tombol Aksi via Keyboard PC:** Oper gigi (*gear up/down*), rem tangan (*handbrake*), dan kamera dikendalikan langsung lewat *keyboard* PC, sehingga *payload* jaringan tidak perlu membawa data tombol aksi.
* **Jalur Recenter Khusus:** Sediakan tombol kecil di bagian tengah atas layar HP (tidak bertumpuk dengan area pedal) serta *hotkey* global di PC untuk mengatur ulang titik tengah kemudi.
* **Non-Goals (Di Luar Cakupan):** Proyek ini tidak menargetkan fitur *force feedback* mekanis (opsional: cukup meneruskan efek *rumble* XInput ke `navigator.vibrate` di Android) dan tidak ditujukan untuk game dengan sistem *anti-cheat* online.

---

## 2. Arsitektur Koneksi & Keamanan

### A. Jalur Koneksi
1. **Jalur Utama (USB + ADB Reverse):** Gunakan koneksi kabel USB dengan perintah `adb reverse tcp:8000 tcp:8000` dan akses melalui `http://localhost:8000` di Chrome Android. Alamat `localhost` otomatis dianggap sebagai *secure context* oleh browser sehingga sensor gerak dapat diakses tanpa sertifikat SSL/HTTPS dan tanpa peringatan keamanan, memberikan latensi paling stabil sekaligus mengisi daya HP.
2. **Jalur Cadangan (Wi-Fi LAN):** Gunakan HTTPS dengan sertifikat `mkcert` (alamat IP PC wajib dimasukkan ke dalam sertifikat) atau panduan penggunaan *flag* Chrome `unsafely-treat-insecure-origin-as-secure` untuk `http://IP-PC:8000`.
3. **Autentikasi & QR Code:** Untuk jalur Wi-Fi, server wajib menampilkan QR code berisi URL yang dilengkapi token autentikasi sederhana agar pengguna lain di jaringan LAN yang sama tidak dapat mengambil alih input PC.

### B. Target Latensi & Jaringan
* Tetapkan target latensi *end-to-end* di bawah **40 ms**.
* Aktifkan opsi `TCP_NODELAY` pada koneksi WebSocket di server untuk menekan efek *jitter*.
* Ukur latensi menggunakan mekanisme **ping/pong RTT** (bukan membandingkan *timestamp* satu arah karena jam antara HP dan PC tidak sinkron) yang sekaligus berfungsi sebagai *heartbeat*.

---

## 3. Arsitektur Server PC & Abstraksi Virtual Controller

* **Framework Server:** Gunakan Python (FastAPI + Uvicorn / WebSockets) untuk menyajikan halaman web statis sekaligus menangani komunikasi WebSocket.
* **Driver & Library Utama:** Gunakan `vgamepad` di atas driver **ViGEmBus** (wajib diinstal manual dari rilis terbaru di GitHub untuk menghindari komponen *updater* versi v1.21.442 ke bawah yang berisiko membocorkan IP publik).
* **Verifikasi API:** Verifikasi pemanggilan method `vgamepad` sesuai dokumentasi resminya (seperti `left_joystick_float`, `right_trigger_float`, `left_trigger_float`, dan kewajiban memanggil `update()`)[cite: 1].
* **Antarmuka Abstraksi (`VirtualController`):** Bungkus implementasi `vgamepad` di balik satu kelas antarmuka `VirtualController` yang memiliki method standar: `set_steer(val)`, `set_throttle(val)`, `set_brake(val)`, dan `update()`[cite: 1]. Hal ini memastikan *backend* dapat diganti ke `vJoy` (DirectInput) di masa depan tanpa perlu mengubah kode lainnya[cite: 1].
* **Loop Update Terpisah:** Pisahkan proses penerimaan paket jaringan dari *loop* pembaruan *controller* virtual; jalankan pemanggilan `update()` pada laju tetap (**125 Hz hingga 250 Hz**) menggunakan nilai input terbaru yang tersimpan di memori[cite: 1].

---

## 4. Pembacaan Sensor & Pipeline Pemrosesan Sinyal

### A. Pembacaan Sudut Kemudi di Klien (Bebas Gimbal Lock)
* **Jangan gunakan sudut Euler (`beta`/`gamma`)** dari `DeviceOrientationEvent` secara langsung karena saat HP dipegang tegak seperti setir (`beta` mendekati 90 derajat), nilai akan mengalami *gimbal lock* dan melompat-lompat[cite: 1].
* **Jangan gunakan `AbsoluteOrientationSensor`** karena sensor ini bergantung pada magnetometer yang mudah terganggu oleh medan magnet speaker atau monitor di meja[cite: 1].
* **Metode yang Dikunci:** Hitung sudut kemiringan menggunakan **Vektor Gravitasi** (`accelerationIncludingGravity` dari `DeviceMotionEvent`, lalu hitung sudutnya dengan fungsi `atan2` pada dua sumbu yang sejajar dengan layar) agar sepenuhnya bebas dari *yaw drift*[cite: 1]. Sediakan opsi cadangan menggunakan `RelativeOrientationSensor` (quaternion dari gabungan gyro dan akselerometer)[cite: 1].

### B. Pemrosesan Terpusat di Server
* Klien hanya bertugas mengirim sudut kemiringan mentah (dalam derajat) dan nilai bukaan pedal[cite: 1].
* Seluruh perhitungan *recenter*, *max angle*, filter sinyal, *deadzone*, dan kurva *gamma* wajib dieksekusi di **server** agar logika terpusat di satu tempat[cite: 1].
* Perubahan pengaturan secara *real-time* dikirim melalui pesan WebSocket bertipe `config` yang terpisah dari *payload* input utama[cite: 1].

### C. Urutan Pipeline Sinyal Kemudi di Server
1. **Filter Sinyal:** Terapkan **One Euro Filter** (atau *low-pass filter*) dengan parameter yang dapat diatur untuk meredam getaran tangan tanpa menambah *lag* berlebih[cite: 1].
2. **Kalibrasi & Normalisasi:** Kurangi sudut dengan *offset* titik tengah (*recenter*), bagi dengan batas sudut maksimum (*max angle*), lalu batasi (*clamp*) nilainya ke rentang `-1.0` hingga `1.0`[cite: 1].
3. **Deadzone dengan Penskalaan Ulang (Rescaling):** Terapkan *deadzone* tengah dengan penskalaan ulang agar nilai output transisi secara mulus dari `0.0` dan tidak langsung melompat ke nilai batas *deadzone* (misalnya `0.02`)[cite: 1].
4. **Kurva Non-Linear (Gamma):** Terapkan pemangkatan kurva *gamma* untuk menghaluskan kendali di sekitar titik tengah[cite: 1].
5. **Kompensasi Inverse Deadzone (Opsional):** Sediakan pengaturan *inverse deadzone* untuk mengimbangi *deadzone* bawaan di dalam game (umumnya berkisar 20%–25%) agar gerakan kemudi yang kecil tetap terbaca oleh game[cite: 1].

---

## 5. Desain Pedal Layar Penuh (Multi-Touch)

* **API Sentuhan:** Gunakan **Pointer Events** (`pointerdown`, `pointermove`, `pointerup`, `pointercancel`) dengan properti CSS `touch-action: none` (bukan `TouchEvent` lama)[cite: 1]. Lacak setiap jari berdasarkan `pointerId` agar kedua jempol (rem di kiri, gas di kanan) dapat bekerja secara independen dan bersamaan[cite: 1].
* **Pencegahan Gestur Browser:** Matikan seluruh gestur bawaan browser seperti *zoom*, *scroll*, *long-press* menu konteks, dan seleksi teks, serta gunakan mode *fullscreen* imersif untuk meminimalisir bentrokan dengan gestur *back* sistem Android di tepi layar[cite: 1].
* **Mode Respons Pedal (Dapat Dipilih di Pengaturan):**
  1. **Mode Progresif (Default):** Nilai bukaan gas/rem (`0.0` sampai `1.0`) dihitung secara proporsional berdasarkan pergeseran posisi vertikal jari relatif terhadap titik sentuh awal[cite: 1].
  2. **Mode Biner dengan Ramp Halus:** Sentuhan berfungsi sebagai tombol penuh tetapi nilainya naik dan turun secara bertahap (*ramp* selama 100–150 ms) agar mobil tidak menghentak[cite: 1].
* **Umpan Balik (Feedback):** Tampilkan indikator visual (perubahan warna atau tinggi bar) untuk nilai gas dan rem saat ini, indikator status koneksi dan latensi (ms), serta getaran singkat (`navigator.vibrate`) saat jempol pertama kali menyentuh pedal[cite: 1].

---

## 6. Format Payload & Mekanisme Failsafe

### A. Format Payload Input (60Hz+)
Kirimkan *payload* ringkas yang hanya memuat tiga nilai utama dan nomor urut paket (`seq`) untuk membuang paket yang datang terlambat/tidak berurutan[cite: 1]:
```json
{
  "seq": 1042,
  "s": -12.4,
  "t": 0.85,
  "b": 0.0
}
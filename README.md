# DPiRS — 受信専用 D-STAR D-PRS i-Gate

[English](README.en.md)

MMDVMホットスポットボード（MMDVM_HS / ADF7021）で、**誰でも**自分のD-STAR無線機から位置情報を送るだけで、APRS（aprs.fi など）に表示される i-Gate を作るプログラムです。

- 指定した周波数（既定 438.010 MHz）で、D-STAR の DV モード（シンプレックス）の送信を受信します。
- 音声に付随する **D-PRS（GPS位置情報）** を取り出し、CRCを検証して、APRS-IS に送ります。
- リピータ／ゲートウェイの設定（RPT1・RPT2・UR）を **一致させる必要はありません**。DVモードで送れば拾えます。
- **送信は一切しません。** モデムには設定・状態確認のコマンドしか書き込まない作りで（`modem.py`、テストあり）、送信データのコマンドはコード上で拒否されます。
- MMDVMHost・ircDDBGateway・Pi-Star は **不要** です（同じボードを同時には使えません）。

```
無線機 (DV, 438.010MHz) ──電波──> [MMDVMボード] ──シリアル──> DPiRS ──> APRS-IS ──> aprs.fi
```

## 動作確認済みの環境

| 項目 | 確認済み | 未確認（動くかもしれません） |
|---|---|---|
| コンピュータ | Raspberry Pi 3B | Pi 4 / Zero 系（インストーラは対応、未検証）、Pi 5（手動設定が必要） |
| OS | Raspberry Pi OS Trixie（Pi 3B で確認） | Bookworm（インストーラは対応、未検証） |
| モデム | AURSINC MMDVM ホットスポットボード（GPIO、ファーム MMDVM_HS_Hat v1.5.2 / ADF7021） | USBタイプ、他のADF7021系MMDVM_HS |
| 無線機 | ID-52、ID-31、ID-31PLUS、IC-7100（DVモード・シンプレックス） | IC-705 など |

## できないこと・制限

- **D-STAR D-PRS 専用**です。APRS（144 MHz帯 AFSK 1200bps）は受信できません。
- 待ち受ける周波数は **1つだけ** です（ボードが同時に待ち受けられるのは1周波数）。
- 位置を **NMEA形式**（`$GPRMC` など）で送る設定の無線機は未対応です（受信してログには出ますが、APRS-ISには送りません）。D-PRS形式に設定してください。
- **送信はしません。** 応答（ACK）や、受信できたかの返答もありません。
- MMDVMHost / Pi-Star などと同じボードを同時に使うことはできません。

## かんたんインストール（Raspberry Pi OS Bookworm 以降）

```bash
sudo apt install -y git
git clone https://github.com/Nickel2Plus/DPiRS.git
cd DPiRS
./install.sh
```

`install.sh` が行うこと（`config.txt` と `cmdline.txt` は、変更前に自動でバックアップします）：

1. `python3-serial` の導入
2. （GPIO接続の場合）UARTの有効化、Bluetoothの切り離し、シリアルコンソールの無効化
3. `dialout` グループへの追加
4. `config.ini` の作成（コールサインと、自局位置を地図に表示するかを聞かれます）
5. systemd サービス `dpirs` の登録

UART設定を変更した場合は、**再起動**が必要です（`sudo reboot`）。

### 動作確認

```bash
python3 check.py                 # 診断（設定・シリアル・モデム／サービスの状態・ネットワーク）
journalctl -u dpirs -f        # ログを見る
```

無線機を **DVモード・438.010 MHz・シンプレックス** にして送信してください。次のようなログが出れば成功です。

```
RX MY='XXXXXX  '/'ID31' UR='CQCQCQ  ' ...
D-PRS [CRC OK]: $$CRC....,XXXXXX>API31,DSTAR*:!....
  [gate] DRY-RUN: XXXXXX>API31,DSTAR*,qAR,YOURCALL-10:!....
```

### APRS-ISへ送出する（位置が公開されます）

最初は `send = no`（ドライラン）です。動作を確認できたら、`config.ini` を編集します。

```bash
nano config.ini        # [aprs_is] send = yes に変更
sudo systemctl restart dpirs
```

> ⚠ `send = yes` にすると、受信した **すべての局の位置** が APRS-IS に流れ、aprs.fi などで公開されます。ご自身の運用場所・周波数の周知を含め、責任をもって運用してください。

## 設定（config.ini）

`nano config.ini` や `vi config.ini` で編集します。変更後は `sudo systemctl restart dpirs`。

| セクション | 項目 | 既定 | 説明 |
|---|---|---|---|
| general | language | auto | メッセージの言語（auto / ja / en） |
| station | callsign | NOCALL-10 | **必須**。自局のコールサイン＋SSID（例 JA1ABC-10） |
| modem | serial_port | /dev/serial0 | GPIO接続は `/dev/serial0`、USBは `/dev/ttyACM0` |
| modem | rx_frequency | 438010000 | 受信周波数（Hz） |
| modem | frequency_offset | 0 | 周波数ずれの補正（Hz） |
| modem | rx_invert | no | 受信できない場合のみ yes を試す |
| aprs_is | send | no | yes でAPRS-ISへ送出 |
| aprs_is | server / server_port | asia.aprs2.net / 14580 | 接続先 |
| aprs_is | passcode | （空） | 空ならコールサインから自動計算 |
| filter | dupe_seconds | 30 | 同じ局の同じ内容を破棄する秒数 |
| filter | min_interval | 10 | 同じ局からの異なるパケットの最小間隔（秒） |
| beacon | enabled / latitude / longitude / comment / interval_minutes | no | このi-Gate自身の位置をAPRSに表示する（任意）。**座標が公開されます**。自宅なら近くの別の地点を推奨。`send = yes` のときだけ送出。install.sh でも設定できます |

## 無線機を使う人向けの案内

- **DVモード**、周波数は運用者が指定した周波数（例 438.010 MHz）、デュプレックスは OFF（シンプレックス）。
- GPS送信設定を **D-PRS** にします（NMEA形式は不可）。位置は **音声送信に付随して** 送られるので、PTTを押す（または GPS自動送信を使う）必要があります。
- ⚠ **送った位置は APRS-IS に流れ、aprs.fi などで誰でも見られる形で公開されます。** 自宅や移動中の位置を公開したくない場合は、送信しないでください。
- 位置が取れるまでに必要な送信時間の目安: ID-31 約0.6秒、IC-7100 約0.7秒、ID-31PLUS 約0.9秒、ID-52 約1秒。**1秒以上** 送信してください。
- ID-52 は、短い送信でも欠落した部分をCRCで検証しながら復元して拾います（検証に合格したデータだけを送ります）。

## 仕組みと安全性

- 受信したD-STARのスローデータから D-PRS の行（`$$CRCxxxx,...`）を組み立て、CRC-CCITTが一致したものだけを使います。
- 送信元コールの形式チェック、`NOGATE` / `RFONLY` パスの除外、重複の破棄、送信元ごとのレート制限を行います。
- APRS-IS への送出は別スレッドで行い、ネットワーク障害中も受信を止めません（自動再接続）。
- モデムから応答がなくなった場合は終了し、systemd が再起動します。

## トラブルシューティング

まず `python3 check.py` を実行してください。主な原因：

| 症状 | 原因と対処 |
|---|---|
| `/dev/serial0` がない | UART未設定。`./install.sh` を実行して再起動 |
| `GET_VERSION` に応答がない／`multiple access` | シリアルコンソール（`agetty`）などがポートを使っている。`install.sh` を再実行して再起動 |
| Permission denied | `sudo usermod -aG dialout $USER` を実行し、再ログイン |
| 何も受信できない | 無線機の設定を確認。出力を下げて距離をとる。`rx_invert = yes` を試す。周波数のずれは `frequency_offset` で補正 |
| `CRC NG` が出る | 受信の取りこぼし。送信を長めに、近い距離で |
| `login was not verified` | コールサイン／パスコードを確認 |
| 手動実行で `multiple access` | サービスが動作中です。`sudo systemctl stop dpirs` で止めてから実行し、終わったら `sudo systemctl start dpirs` |

手動でUARTを設定する場合（Pi 5 など未対応機種）：UART（GPIO14/15）をモデムに割り当て、シリアルコンソールを無効にし、`/dev/serial0` または該当デバイスを `serial_port` に指定してください。

## アンインストール

```bash
./uninstall.sh
```

サービスと `config.txt` の追記を削除します（`config.ini` とプログラムは残します）。

`cmdline.txt` のシリアルコンソールの指定は **自動では戻しません**（インストール時に作ったバックアップ `cmdline.txt.dpirs-*.bak` から、手動で戻せます）。再インストール（`./install.sh`）はやり直せます（設定済みの箇所は変更しません）。

## テスト

```bash
python3 -m unittest discover -s tests -v
```

無線機・ネットワーク不要の合成データによるテストです。

## 注意

- このプログラムを使ってAPRS-ISに接続するには、**有効なアマチュア無線のコールサイン**が必要です。ご自身のコールサインで運用してください。
- 各国・地域の電波法令・運用規則に従ってください。このプログラムは送信しませんが、運用の責任は運用者にあります。
- 受信した位置情報（他の局のものを含む）は公開されます。

## 謝辞・参考

- MMDVM のシリアルプロトコルは、G4KLX（Jonathan Naylor）氏の [MMDVM](https://github.com/g4klx/MMDVMHost) と、CA6JAU 氏の MMDVM_HS のソースを参考にしました。コードは独自に書いています。
- D-STAR は Icom／JARL が策定した規格です。このプログラムは、それらの団体や各メーカーとは関係ありません。

## ライセンス

GNU General Public License v3.0（[LICENSE](LICENSE)）

Copyright (C) 2026 JQ7EAH (GitHub: Nickel2Plus)

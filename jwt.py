import os, sys, json, time, random, string, hashlib, hmac, uuid, base64, subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests, urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
from google.protobuf.internal import builder as _builder

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# URLS & CONSTANTS
# ============================================================
URL_TOKEN_GRANT = "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant"
URL_MAJOR_LOGIN = "https://loginbp.mainecoonnodex.com/MajorLogin"
URL_GET_LOGIN_DATA = "https://clientbp.ppmainecoonghj.com/GetLoginData"

APP_ID        = 100067
CLIENT_SECRET = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
RELEASE_VER   = "OB55"
GAME_VERSION  = "2.132.4"

AES_KEY = bytes([89,103,38,116,99,37,68,69,117,104,54,37,90,99,94,56])
AES_IV  = bytes([54,111,121,90,68,114,50,50,69,51,121,99,104,106,77,37])

HEADERS_MSDK = {
    "User-Agent": "GarenaMSDK/4.0.44(SM-G570F;Android 8.0.0;en;GB;app 1.132.1 2019121228;)", 
    "Content-Type": "application/json; charset=utf-8", 
    "Connection": "keep-alive"
}

HEADERS_LOGINBP = {
    'Host': 'loginbp.mainecoonnodex.com', 
    'User-Agent': 'UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)', 
    'Accept': '*/*', 
    'Accept-Encoding': 'deflate, gzip', 
    'Authorization': 'Bearer', 
    'X-GA': 'v1 1', 
    'ReleaseVersion': RELEASE_VER, 
    'Content-Type': 'application/x-www-form-urlencoded', 
    'X-Unity-Version': '2018.4.12f1'
}

# ============================================================
# TYPEDEFS & HELPERS
# ============================================================
def _tf(t): return {'type': t, 'name': ''}
typedef_login = {'3':_tf('bytes'),'4':_tf('bytes'),'5':_tf('int'),'7':_tf('bytes'),'8':_tf('bytes'),'9':_tf('bytes'),'10':_tf('bytes'),'11':_tf('bytes'),'12':_tf('int'),'13':_tf('int'),'14':_tf('bytes'),'15':_tf('bytes'),'16':_tf('int'),'17':_tf('bytes'),'18':_tf('bytes'),'19':_tf('bytes'),'20':_tf('bytes'),'21':_tf('bytes'),'22':_tf('bytes'),'23':_tf('bytes'),'24':_tf('bytes'),'25':_tf('bytes'),'26':_tf('bytes'),'29':_tf('bytes'),'30':_tf('int'),'41':_tf('bytes'),'42':_tf('bytes'),'57':_tf('bytes'),'60':_tf('int'),'61':_tf('int'),'62':_tf('int'),'63':_tf('int'),'64':_tf('int'),'65':_tf('int'),'66':_tf('int'),'67':_tf('int'),'73':_tf('int'),'74':_tf('bytes'),'76':_tf('int'),'77':_tf('bytes'),'78':_tf('int'),'79':_tf('int'),'81':_tf('bytes'),'83':_tf('bytes'),'85':_tf('int'),'86':_tf('bytes'),'87':_tf('int'),'88':_tf('int'),'92':_tf('int'),'93':_tf('bytes'),'94':_tf('bytes'),'96':_tf('bytes'),'97':_tf('int'),'98':_tf('int'),'99':_tf('bytes'),'100':_tf('bytes'),'102':_tf('bytes'),'104':_tf('int'),'105':_tf('int'),'106':_tf('bytes'),'107':_tf('bytes')}

def enc_aes(data):
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(data, AES.block_size))

def dec_aes(data):
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    try:
        return unpad(cipher.decrypt(data), AES.block_size)
    except Exception:
        return cipher.decrypt(data)

def _build_login_meta(open_id, access_token):
    return {
        '3': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S').encode(),
        '4': b'free fire', '5': 1, '7': GAME_VERSION.encode(), '8': b'Android OS 12',
        '9': b'Handheld', '10': b'Jio', '11': b'WIFI', '12': 1280, '13': 720, '14': b'240',
        '16': 6000, '17': b'Adreno (TM) 640', '18': b'OpenGL ES 3.2', '19': f'Google|{uuid.uuid4()}'.encode(),
        '20': b'49.36.180.10', '21': b'en', '22': open_id.encode(), '23': b'4', '24': b'Handheld',
        '25': b'Asus', '26': b'IND', '29': access_token.encode(), '30': 1,
        '41': b'Jio', '42': b'WIFI', '57': b'1ac4b80ecf0478a44203bf8fac6120f5',
        '60': 30000, '61': 30000, '62': 2519, '63': 243, '64': 32357, '65': 34308,
        '66': 32357, '67': 34308, '73': 1, '76': 2, '78': 2, '79': 1, '81': b'32',
        '83': b'2019118525', '85': 3, '86': b'OpenGLES3', '87': 4095, '88': 4,
        '92': 20000, '93': b'android_max', '97': 1, '98': 1, '99': b'4', '100': b'4',
        '104': 77149, '105': 1
    }

# ============================================================
# EMBEDDED PROTOBUF (replaces MajorLoginCapture_pb2.py - no separate pb2 file needed)
# ============================================================
# Equivalent .proto (field names marked "unknown" come from the capture, real names not recoverable):
#
# syntax = "proto3";
#
# package ffcap;
#
# message MajorLoginReq {
#   string event_time = 3;
#   string game_name = 4;
#   int32 platform_id = 5;
#   string client_version = 7;
#   string system_software = 8;
#   string system_hardware = 9;
#   string telecom_operator = 10;
#   string network_type = 11;
#   uint32 screen_width = 12;
#   uint32 screen_height = 13;
#   string screen_dpi = 14;
#   string processor_details = 15;
#   uint32 memory = 16;
#   string gpu_renderer = 17;
#   string gpu_version = 18;
#   string unique_device_id = 19;
#   string client_ip = 20;
#   string language = 21;
#   string open_id = 22;
#   string open_id_type = 23;
#   string device_type = 24;
#   string field_25 = 25;  // capture: plain string (thunderFF typed this as a message)
#   string field_26 = 26;  // name unknown (from capture)
#   string access_token = 29;
#   int32 platform_sdk_id = 30;
#   string network_operator_a = 41;
#   string network_type_a = 42;
#   string client_using_version = 57;
#   int32 external_storage_total = 60;
#   int32 external_storage_available = 61;
#   int32 internal_storage_total = 62;
#   int32 internal_storage_available = 63;
#   int32 game_disk_storage_available = 64;
#   int32 game_disk_storage_total = 65;
#   int32 external_sdcard_avail_storage = 66;
#   int32 external_sdcard_total_storage = 67;
#   int32 login_by = 73;
#   string library_path = 74;
#   int32 reg_avatar = 76;
#   string library_token = 77;
#   int32 channel_type = 78;
#   int32 cpu_type = 79;
#   string cpu_architecture = 81;
#   string client_version_code = 83;
#   int32 field_85 = 85;  // name unknown (from capture)
#   string graphics_api = 86;
#   uint32 supported_astc_bitset = 87;
#   int32 login_open_id_type = 88;
#   bytes analytics_detail = 89;
#   uint32 loading_time = 92;
#   string release_channel = 93;
#   string extra_info = 94;
#   uint32 android_engine_init_flag = 95;
#   string field_96 = 96;  // name unknown (from capture)
#   int32 if_push = 97;
#   int32 is_vpn = 98;
#   string origin_platform_type = 99;
#   string primary_platform_type = 100;
#   bytes field_102 = 102;  // name unknown (from capture)
#   int32 field_104 = 104;  // name unknown (from capture)
#   int32 field_105 = 105;  // name unknown (from capture)
#   string field_106 = 106;  // name unknown (from capture)
#   string field_107 = 107;  // name unknown (from capture)
# }
#
# message MajorLoginRes {
#   uint64 account_id = 1;
#   string region = 2;
#   string token = 8;
#   string url = 10;
#   uint64 server_time = 21;
#   bytes aes_ak = 22;
#   bytes iv_i = 23;
# }
#
# message GetLoginData {
#   uint64 AccountUID = 1;
#   string Region = 3;
#   string AccountName = 4;
#   string Online_IP_Port = 14;
#   int64 Clan_ID = 20;
#   string AccountIP_Port = 32;
#   string Clan_Compiled_Data = 55;
# }
#
_sym_db = _symbol_database.Default()
DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x17MajorLoginCapture.proto\x12\x05ffcap"\xfb\x0b\n\rMajorLoginReq\x12\x12\n\nevent_time\x18\x03 \x01(\t\x12\x11\n\tgame_name\x18\x04 \x01(\t\x12\x13\n\x0bplatform_id\x18\x05 \x01(\x05\x12\x16\n\x0eclient_version\x18\x07 \x01(\t\x12\x17\n\x0fsystem_software\x18\x08 \x01(\t\x12\x17\n\x0fsystem_hardware\x18\t \x01(\t\x12\x18\n\x10telecom_operator\x18\n \x01(\t\x12\x14\n\x0cnetwork_type\x18\x0b \x01(\t\x12\x14\n\x0cscreen_width\x18\x0c \x01(\r\x12\x15\n\rscreen_height\x18\r \x01(\r\x12\x12\n\nscreen_dpi\x18\x0e \x01(\t\x12\x19\n\x11processor_details\x18\x0f \x01(\t\x12\x0e\n\x06memory\x18\x10 \x01(\r\x12\x14\n\x0cgpu_renderer\x18\x11 \x01(\t\x12\x13\n\x0bgpu_version\x18\x12 \x01(\t\x12\x18\n\x10unique_device_id\x18\x13 \x01(\t\x12\x11\n\tclient_ip\x18\x14 \x01(\t\x12\x10\n\x08language\x18\x15 \x01(\t\x12\x0f\n\x07open_id\x18\x16 \x01(\t\x12\x14\n\x0copen_id_type\x18\x17 \x01(\t\x12\x13\n\x0bdevice_type\x18\x18 \x01(\t\x12\x10\n\x08field_25\x18\x19 \x01(\t\x12\x10\n\x08field_26\x18\x1a \x01(\t\x12\x14\n\x0caccess_token\x18\x1d \x01(\t\x12\x17\n\x0fplatform_sdk_id\x18\x1e \x01(\x05\x12\x1a\n\x12network_operator_a\x18) \x01(\t\x12\x16\n\x0enetwork_type_a\x18* \x01(\t\x12\x1c\n\x14client_using_version\x189 \x01(\t\x12\x1e\n\x16external_storage_total\x18< \x01(\x05\x12"\n\x1aexternal_storage_available\x18= \x01(\x05\x12\x1e\n\x16internal_storage_total\x18> \x01(\x05\x12"\n\x1ainternal_storage_available\x18? \x01(\x05\x12#\n\x1bgame_disk_storage_available\x18@ \x01(\x05\x12\x1f\n\x17game_disk_storage_total\x18A \x01(\x05\x12%\n\x1dexternal_sdcard_avail_storage\x18B \x01(\x05\x12%\n\x1dexternal_sdcard_total_storage\x18C \x01(\x05\x12\x10\n\x08login_by\x18I \x01(\x05\x12\x14\n\x0clibrary_path\x18J \x01(\t\x12\x12\n\nreg_avatar\x18L \x01(\x05\x12\x15\n\rlibrary_token\x18M \x01(\t\x12\x14\n\x0cchannel_type\x18N \x01(\x05\x12\x10\n\x08cpu_type\x18O \x01(\x05\x12\x18\n\x10cpu_architecture\x18Q \x01(\t\x12\x1b\n\x13client_version_code\x18S \x01(\t\x12\x10\n\x08field_85\x18U \x01(\x05\x12\x14\n\x0cgraphics_api\x18V \x01(\t\x12\x1d\n\x15supported_astc_bitset\x18W \x01(\r\x12\x1a\n\x12login_open_id_type\x18X \x01(\x05\x12\x18\n\x10analytics_detail\x18Y \x01(\x0c\x12\x14\n\x0cloading_time\x18\\ \x01(\r\x12\x17\n\x0frelease_channel\x18] \x01(\t\x12\x12\n\nextra_info\x18^ \x01(\t\x12 \n\x18android_engine_init_flag\x18_ \x01(\r\x12\x10\n\x08field_96\x18` \x01(\t\x12\x0f\n\x07if_push\x18a \x01(\x05\x12\x0e\n\x06is_vpn\x18b \x01(\x05\x12\x1c\n\x14origin_platform_type\x18c \x01(\t\x12\x1d\n\x15primary_platform_type\x18d \x01(\t\x12\x11\n\tfield_102\x18f \x01(\x0c\x12\x11\n\tfield_104\x18h \x01(\x05\x12\x11\n\tfield_105\x18i \x01(\x05\x12\x11\n\tfield_106\x18j \x01(\t\x12\x11\n\tfield_107\x18k \x01(\t"\x82\x01\n\rMajorLoginRes\x12\x12\n\naccount_id\x18\x01 \x01(\x04\x12\x0e\n\x06region\x18\x02 \x01(\t\x12\r\n\x05token\x18\x08 \x01(\t\x12\x0b\n\x03url\x18\n \x01(\t\x12\x13\n\x0bserver_time\x18\x15 \x01(\x04\x12\x0e\n\x06aes_ak\x18\x16 \x01(\x0c\x12\x0c\n\x04iv_i\x18\x17 \x01(\x0c"\xa4\x01\n\x0cGetLoginData\x12\x12\n\nAccountUID\x18\x01 \x01(\x04\x12\x0e\n\x06Region\x18\x03 \x01(\t\x12\x13\n\x0bAccountName\x18\x04 \x01(\t\x12\x16\n\x0eOnline_IP_Port\x18\x0e \x01(\t\x12\x0f\n\x07Clan_ID\x18\x14 \x01(\x03\x12\x16\n\x0eAccountIP_Port\x18  \x01(\t\x12\x1a\n\x12Clan_Compiled_Data\x187 \x01(\tb\x06proto3')
_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'MajorLoginCapture_pb2', _globals)
# -> defines MajorLoginReq, MajorLoginRes, GetLoginData

# ============================================================
# CAPTURE-BASED MAJORLOGIN / GETLOGINDATA (capture embedded below, no external files)
# ============================================================
MAJOR_LOGIN_HOST = "loginbp.ppmainecoonghj.com"
# Your capture.txt (encrypted MajorLogin request), embedded so no external file is needed.
CAPTURE_HEX = (
    "66A334B0E53272B7F62193A222B880260F5FCFC044F4100D48CCC3F5EF48622693BC7AAEF868038E5F3B413947CC9BA1"
    "635F6D2B6465430E6FD72880AEA2B636D66A27C8C9B58CC0CF6920BCC700021908AC82BEE64501791FAA19717AB863AD"
    "C9C7AA895A3840F49B53527B6E2EB030D788C25E4E9BAEE527EEFC58D99A2A16B8DA2405B43787AA448CBE486657EC64"
    "4AF04B25600B9AF96B6AC807312C03DDDD2E1D0E2DC0CF01E6EC3577990044DBF190A0C16422C7491B7ABD05EE57C5F7"
    "455FF9FFA0736656369D59B3C1445508F38ABB785E7C521EDF364E3F2AF799DA4643F25844F52E09E3F64AF4B78B1A46"
    "6C56BF89BA0D078A588DD42DCA1C1A7C88619394544721AE22A1F1FA6895FFE79F85AADFFF31215A8D34DFA88089A97A"
    "E1E5B4A5544CE6B6B4EEA1950A9E232F64DDB3683C46DFF24D2709B48971BDA41700CFD8C332054F418A588683493E27"
    "730D5329F0504DEB67B67AC4207E0C229F298868EABD1BE4BC2C1998B59B1D13674C2FAA9AB541B7512B996820FB3DD3"
    "CB08B862C4AEC8145574A45BF96E87768275D2AC75F5CA35A887E7F1ADE4B5801DB1C6EA29526505497A256C0A0859EE"
    "E78A5B323D8A17E7D127B0F18B8F2CDBA4955C32C85CEF2279981C8CA25F4634146DA6297114A9CC8617511E6B2CA862"
    "CB0914639CE3C8EC89B4E026D30E381F117EB057F71BE45D3938EC4757574AD11FE34C61036EBDDFF9FC0EEF96F679AB"
    "0F6A26EF35DCF1C86D6CA808225104AE0A60E915D0269AB0C2F6AE0BD9D6FD7DA9715CD7F70DA7A9C0AA4C1BFB4E625B"
    "21D4E0BA387BDAE4619387811F1312FFA7293295D3CCD836567C6C6F29823D61039674F2DDCB6E18BB773E6B28434E0D"
    "9DC4E4BEB749001F32312079C5F7012070109B474421D5D6D9EFB2BC7480F72D5F911450EE243C99A2E35FD8B6DB8D84"
    "B762A30DAD51F6A7C3FB7B62C6DE5A1C62AF3AD610C99ABE868AAA3E0DEA2F42D0DF459F34757F1C080D9FDA4A045BA5"
    "440AED0FF0FCE5A9325B25DA78A61FBEA3A37C6074408C95CA387DA8E68DC8CF6DADBAE4BED2E96A4A58BBA313E2EEBC"
    "CC7A46D6922E7E4D96134431EF41432C1B75AF1E97EA29201B6C14E2C849CB30D3C95FF4322A1E84E944C03FE59F01BD"
    "FCEAD90C6C7148ABDD3E23D1C8DAD30ECED26234281DB0313CBF27DA80456A242AF7D9E607E32CB92C99A7AF9EE9B8A0"
    "30FD69C7A43DDE06DD545CA837A6924F178F0B6B1E2603E42FC684DF9602F6FC4727A6A39A3F14240D5B5E1FBD02E799"
    "21AD55D32B6E33AA32FBC5C64E90201F8B05BC9E920A6A677C4E13C8A1F227BDB785BCCC1F2D11B80863177C7EC6A11D"
    "4F52186AF568A5BF03786ADFDF4BD6203FBD56D55B50F9294644B3000EED9545187257DDA2EC40E4AD4D422647D4B82E"
    "DB0ADCCA928809E854AE1E198D4385AAC25BAB5B1E9FC83F66DF205D016ED891DC42BFAB8050428E631B3EDC6018AF50"
    "8D2B28620F10B22EBF0957ADE7786B2494914B27EF260EEC825DD091414642CD84F71D351649D7946D9B2EB778D9E61D"
    "9FFD5E9E3360D928749ACC9F5D85E60D07B867461E8CF80AB41325C43276DD4A"
)


# ============================================================
# PROVEN UPDATES (tcp_auto_ping) - added, original sections above kept
# ============================================================
MAJOR_TEMPLATE_B64 = "GhMyMDI2LTEwLTI3IDE4OjAxOjU4IglmcmVlIGZpcmUoAToIMS4xMzIuMTNCOUFuZHJvaWQgT1MgMTUgLyBBUEktMzUgKEFQM0EuMjQwOTA1LjAxNS5BMi9BMTU1RlhYUzdDWUc0KUoISGFuZGhlbGRSBFpvbmdaBFdJRklg2Qxo7gVyAzQ1MHodQVJNNjQgRlAgQVNJTUQgQUVTIHwgMjIwMCB8IDiAAeg7igEMTWFsaS1HNTcgTUMykgE+T3BlbkdMIEVTIDMuMiB2MS5yMzJwMS0wMWVhYzAuM2QwZjg5MmU3OTBlNDMxOWEyMzU5MTBjZTE1NTlhOGGaAStHb29nbGV8Njk5YTdmNjktZGI5NC00ZDFkLWI3NmUtYWEzZGY2YjFhYjg3ogEOMTUuMjM1LjIyMC4xNDaqAQJlbrIBC19fT1BFTl9JRF9fugEBNMIBCEhhbmRoZWxkygEQc2Ftc3VuZyBTTS1BMTU1RtIBAlBL6gEQX19BQ0NFU1NfVE9LRU5fX/ABAcoCBFpvbmfSAgRXSUZJygMgNzQyOGIyNTNkZWZjMTY0MDE4YzYwNGExZWJiZmViZGbgA6KGDugDyaoB8AOnKoAEyasBiASihg6QBMmrAZgEooYOyAQC0gRaL2RhdGEvYXBwL35+VURRc1dDU1BpNlJ1TEhFaUs5d0VpQT09L2NvbS5kdHMuZnJlZWZpcmV0aC10VVo1bExRd0xRbFRhNEcwUE9UVnVBPT0vbGliL2FybTY04AQB6gR6YjhlMGNkNWUyOTVlZWU0MmY1ODYwZDNjODZlNDgzZGR8L2RhdGEvYXBwL35+VURRc1dDU1BpNlJ1TEhFaUs5d0VpQT09L2NvbS5kdHMuZnJlZWZpcmV0aC10VVo1bExRd0xRbFRhNEcwUE9UVnVBPT0vYmFzZS5hcGvwBAP4BAKKBQI2NJoFCjIwMTkxMjEyMjmoBQOyBQlPcGVuR0xFUzK4Bf8fwAUE4AWbTuoFB2FuZHJvaWTyBXBLcXNIVDJQTjFnd0NOZlVqSGFocU1kTjcyalNHdVU1Q0g3V0t0d3FVL2didERLR3U4SEszMG12dDl5RThWNndTRTM0SW12ZWVxdWJ2VXozWGx3K0daZU1YZzBZNjJGbCtjcE5UeUxIVUpoaFdOTzNt+AXn5AaCBiZ7ImN1cl9yYXRlIjpudWxsLCJzdXBwb3J0X2V0YzIiOmZhbHNlfYgGAZoGATSiBgE0sgYJEAdNFVJUCgFjwAaHxAHIBgHSBooBaHR0cHM6Ly9kbC5icy5mcmVlZmlyZW1vYmlsZS5jb20vbGl2ZS9BQkhvdFVwZGF0ZXMvfGh0dHBzOi8vY29yZS1icy5mcmVlZmlyZW1vYmlsZS5jb20vbGl2ZS9BQkhvdFVwZGF0ZXMvfDlhOWE0OTNkNjQzNDYyNTcwZGM1MDMyMGIyODUzM2Qw2gYSMS4zNmI3NzZlMjU3MGFjZjgy"

HEADERS_GAME = {
    "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
    "Accept": "*/*",
    "Accept-Encoding": "deflate, gzip",
    "X-GA": "v1 1",
    "ReleaseVersion": "OB55",
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Unity-Version": "2018.4.12f1",
}

def read_varint(buf, i):
    result = 0
    shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            break
    return result, i


def encode_varint(n):
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def encode_tag(field, wire):
    return encode_varint((field << 3) | wire)


def parse_fields(buf):
    fields = []
    i = 0
    while i < len(buf):
        tag, i = read_varint(buf, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = read_varint(buf, i)
        elif wire == 2:
            length, i = read_varint(buf, i)
            value = buf[i:i + length]
            i += length
        elif wire == 5:
            value = buf[i:i + 4]
            i += 4
        elif wire == 1:
            value = buf[i:i + 8]
            i += 8
        else:
            break
        fields.append((field, wire, value))
    return fields


def encode_fields(fields):
    out = bytearray()
    for field, wire, value in fields:
        out += encode_tag(field, wire)
        if wire == 0:
            out += encode_varint(value)
        elif wire == 2:
            out += encode_varint(len(value)) + value
        else:
            out += value
    return bytes(out)


def field_map(fields):
    result = {}
    for field, _wire, value in fields:
        result.setdefault(field, []).append(value)
    return result


def set_string_field(fields, number, new_value):
    new_bytes = new_value.encode() if isinstance(new_value, str) else new_value
    for idx, (field, wire, _value) in enumerate(fields):
        if field == number and wire == 2:
            fields[idx] = (field, wire, new_bytes)
            return fields
    for idx, (field, _wire, _value) in enumerate(fields):
        if field > number:
            fields.insert(idx, (number, 2, new_bytes))
            return fields
    fields.append((number, 2, new_bytes))
    return fields


def as_text(value):
    if isinstance(value, bytes):
        try:
            text = value.decode()
        except UnicodeDecodeError:
            return None
        if all(32 <= ord(c) < 127 or c in "\n\r\t" for c in text):
            return text
    return None


def aes_cbc_encrypt(data, key, iv):
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len]) * pad_len
    try:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher, algorithms, modes)
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return encryptor.update(padded) + encryptor.finalize()
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_CBC, iv).encrypt(padded)
    except ImportError:
        pass
    result = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc", "-K", key.hex(), "-iv", iv.hex(),
         "-nopad"],
        input=padded, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError("No AES backend available")
    return result.stdout


def decode_jwt_payload(token):
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def now_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def game_headers(authorization=None):
    headers = dict(HEADERS_GAME)
    headers["X-GA-SV"] = str(int(time.time()))
    headers["Authorization"] = authorization if authorization else "Bearer "
    return headers


class _MajorLoginResAdapter:
    def __init__(self, fields):
        def _text(n):
            v = fields.get(n, [b""])[0]
            return v.decode(errors="ignore") if isinstance(v, bytes) else v
        self.account_id = fields.get(1, [0])[0]
        self.region = _text(2)
        self.token = _text(8)
        self.url = _text(10)
        self.server_time = fields.get(21, [0])[0]
        self.aes_ak = fields.get(22, [b""])[0]
        self.iv_i = fields.get(23, [b""])[0]


class _GetLoginDataResAdapter:
    def __init__(self, fields):
        def _text(n):
            v = fields.get(n, [b""])[0]
            return v.decode(errors="ignore") if isinstance(v, bytes) else v
        self.AccountUID = fields.get(1, [0])[0]
        self.Region = _text(3)
        self.AccountName = _text(4)
        self.Online_IP_Port = _text(14)
        self.Clan_ID = fields.get(20, [0])[0]
        self.AccountIP_Port = _text(32)
        self.Clan_Compiled_Data = _text(55)


def base_login_headers(access_token, host):
    return {
        "Accept": "*/*",
        "Accept-Encoding": "deflate, gzip",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": host,
        "ReleaseVersion": "OB55",
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "X-GA": "v1 1",
        "X-GA-SV": "1790245960",
        "X-Unity-Version": "2018.4.12f1"
    }

def build_major_login_payload(new_access_token, new_open_id, new_google_uuid):
    # UPDATED (proven, tcp_auto_ping): template, update 3/19/22/29, keep 94.
    # Original capture-decrypt builder is replaced by this, but the
    # original embedded capture + protobuf sections are kept above
    # in the file, unused by this builder.
    template = base64.b64decode(MAJOR_TEMPLATE_B64)
    fields = parse_fields(template)
    fields = set_string_field(fields, 3, now_string())
    fields = set_string_field(fields, 19, new_google_uuid)
    fields = set_string_field(fields, 22, str(new_open_id))
    fields = set_string_field(fields, 29, str(new_access_token))
    major_body_plain = encode_fields(fields)
    return aes_cbc_encrypt(major_body_plain, AES_KEY, AES_IV)

def major_login(encrypted_bytes, access_token):
    # UPDATED (proven): game_headers() with empty Bearer, current X-GA-SV.
    # access_token is kept in the signature for the original caller;
    # it is NOT sent as the MajorLogin Bearer header.
    # Transport restored to original: requests.post, verify=False.
    headers = game_headers()
    try:
        login_res = requests.post(URL_MAJOR_LOGIN, headers=headers, data=encrypted_bytes, verify=False, timeout=15)
        if login_res.status_code != 200:
            print(f"[-] Server Response Text: {login_res.text}")
            return None
        return login_res.content
    except Exception as e:
        print(f"[-] Network error during MajorLogin: {e}")
        return None

def _dump_fields(tag, field_list):
    # Debug dump: EVERYTHING, full values, no filtering.
    # Used to hunt for the 448 token source (a 32-byte or 64-hex field).
    # One-time test run on test accounts, per user request.
    print(f"[{tag}] all fields (FULL, unfiltered):")
    for field, wire, value in field_list:
        if wire == 0:
            print(f"    f{field}: varint {value}")
        elif isinstance(value, bytes):
            try:
                txt = value.decode("utf-8")
                if all(32 <= ord(c) < 127 for c in txt):
                    print(f"    f{field}: bytes len={len(value)} text={txt!r}")
                else:
                    print(f"    f{field}: bytes len={len(value)} hex={value.hex()}")
            except Exception:
                print(f"    f{field}: bytes len={len(value)} hex={value.hex()}")
        else:
            print(f"    f{field}: wire={wire} len={len(value)}")


def parse_major_login_response(raw_bytes):
    # UPDATED (proven): offset scan for fields 1 and 8.
    # Returned object keeps the original attribute names.
    major_fields = None
    major_list = None
    for offset in [0, 64] + list(range(128)):
        cand_list = parse_fields(raw_bytes[offset:])
        candidate = field_map(cand_list)
        if candidate.get(1) and candidate.get(8):
            major_fields = candidate
            major_list = cand_list
            break
    if not major_fields:
        return _MajorLoginResAdapter({})
    _dump_fields("MajorLogin", major_list)
    return _MajorLoginResAdapter(major_fields)

def get_login_data(base_url, encrypted_bytes, token):
    # UPDATED (proven): decrypt MajorLogin payload, update timestamp,
    # field 99 "4" -> "0", field 103 added = 1, re-encrypt, POST to
    # hardcoded clientbp GetLoginData, Bearer = MajorLogin JWT.
    # base_url is kept in the signature for the original caller and
    # is ignored, as in tcp_auto_ping. Transport restored to
    # original: requests.post, verify=False.
    try:
        plain = dec_aes(encrypted_bytes)
        gld_fields = parse_fields(plain)
        gld_fields = set_string_field(gld_fields, 3, now_string())
        gld_fields = set_string_field(gld_fields, 99, "0")
        gld_fields = set_string_field(gld_fields, 103, b"")
        fixed = []
        for field, wire, value in gld_fields:
            fixed.append((103, 0, 1) if field == 103 else (field, wire, value))
        gld_body = aes_cbc_encrypt(encode_fields(fixed), AES_KEY, AES_IV)
    except Exception as e:
        print(f"[-] Could not build GetLoginData payload: {e}")
        return None
    headers = game_headers(authorization=f"Bearer {token}")
    try:
        res = requests.post(URL_GET_LOGIN_DATA, headers=headers, data=gld_body, verify=False, timeout=15)
        if res.status_code != 200:
            print(f"[-] Server Response Text: {res.text}")
            return None
        return res.content
    except Exception as e:
        print(f"[-] Network error during GetLoginData: {e}")
        return None

def parse_login_data_response(raw_bytes):
    # UPDATED (proven): direct generic field parsing.
    # Original print format kept, plus endpoints (the main GetLoginData result).
    parsed = parse_fields(raw_bytes)
    _dump_fields("GetLoginData", parsed)
    fields = field_map(parsed)
    proto = _GetLoginDataResAdapter(fields)
    print("GetLoginData decoded:")
    print(f"    AccountUID         : {proto.AccountUID}")
    print(f"    Region             : {proto.Region}")
    print(f"    AccountName        : {proto.AccountName}")
    print(f"    Online_IP_Port     : {proto.Online_IP_Port}")
    print(f"    AccountIP_Port     : {proto.AccountIP_Port}")
    print(f"    Clan_ID            : {proto.Clan_ID}")
    return proto


# ============================================================
# EXECUTION FLOW WITH RAW RESPONSE DEBUGGING
# ============================================================
def login_with_existing_account():
    input_uid = input("[?] Enter Account UID: ").strip()
    input_pwd = input("[?] Enter Account Password: ").strip()
    
    if not input_uid or not input_pwd:
        print("[!] UID and Password cannot be empty!")
        return

    session = requests.Session()

    # 1. Token Grant
    dev_id = f"02-{uuid.uuid4()}"
    grant_body = {
        "client_id": APP_ID,
        "client_secret": CLIENT_SECRET,
        "client_type": 2,
        "device_id": dev_id,
        "password": input_pwd,
        "response_type": "token",
        "uid": int(input_uid)
    }
    
    r = session.post(URL_TOKEN_GRANT, headers=HEADERS_MSDK, json=grant_body, verify=False, timeout=10)
    
    if r.status_code != 200:
        print(f"[!] Token Grant failed: {r.text}")
        return
        
    gd = r.json().get("data", {})
    access_token = gd.get("access_token")
    open_id = gd.get("open_id")
    
    if not access_token or not open_id:
        print(f"[!] Failed to parse access_token or open_id from response: {r.text}")
        return
        

    # 2. Major Login (proven template based, tcp_auto_ping)
    try:
        enc_login = build_major_login_payload(access_token, open_id, f"Google|{uuid.uuid4()}")
    except Exception as e:
        print(f"[!] Could not build MajorLogin payload: {e}")
        return

    login_bytes = major_login(enc_login, access_token)
    if not login_bytes:
        return

    try:
        login_proto = parse_major_login_response(login_bytes)
    except Exception as e:
        print(f"[!] Could not parse MajorLogin response: {e}")
        return

    if not login_proto.token:
        print("[!] No token in MajorLogin response - cannot continue to GetLoginData.")
        return

    # MAIN THING - PRINT THE JWT (restored / added):
    # This file is for fetching the JWT, so print it in full here,
    # before GetLoginData, so it is shown even if GetLoginData fails.
    # Keep this output private - it is a live session token.
    print("[+] MajorLogin OK")
    print(f"    AccountID          : {login_proto.account_id}")
    print(f"    Region             : {login_proto.region}")
    print(f"    JWT                : {login_proto.token}")

    # 3. GetLoginData
    login_data_bytes = get_login_data(login_proto.url, enc_login, login_proto.token)
    if not login_data_bytes:
        return

    try:
        parse_login_data_response(login_data_bytes)
    except Exception as e:
        print(f"[!] Could not parse GetLoginData response: {e}")

if __name__ == "__main__":
    login_with_existing_account()

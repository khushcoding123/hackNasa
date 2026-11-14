// ORA bench test: ESP-NOW beacon and RSSI logger for a model mine (pipe or cardboard maze).
//
// Flash the same firmware to every ESP32. Each node broadcasts a small beacon five times a
// second and prints one CSV line for every beacon it hears:
//
//     millis,my_id,from_id,sequence,rssi_dbm
//
// Set NODE_ID per board in platformio.ini (build_flags = -DNODE_ID=2). Capture the serial
// output of the moving node, add the tunnel distance and bend angle for each position, and fit
// the tunnel loss model with hardware/fit_tunnel_model.py.
//
// NOT YET COMPILED OR RUN: written against the Arduino-ESP32 2.x ESP-NOW API. Build it with
// PlatformIO before relying on it.
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#ifndef NODE_ID
#define NODE_ID 1
#endif

static const uint8_t BROADCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static const uint8_t CHANNEL = 1;
static const uint32_t BEACON_MS = 200;
// A fixed node drops out of the breadcrumb role until the mover's link to it is this weak.
static const int DROP_THRESHOLD_DBM = -80;

struct Beacon {
  uint32_t magic;     // 'ORA1'
  uint8_t node;
  uint32_t sequence;
};

static uint32_t sequence = 0;
static volatile int lastRssi = 0;

// ESP-NOW does not pass RSSI to the receive callback in core 2.x, so read it from the
// promiscuous-mode packet header of the most recent management/action frame.
static void sniff(void *buffer, wifi_promiscuous_pkt_type_t type) {
  if (type != WIFI_PKT_MGMT) return;
  lastRssi = ((wifi_promiscuous_pkt_t *)buffer)->rx_ctrl.rssi;
}

static void onReceive(const uint8_t *mac, const uint8_t *data, int length) {
  if (length != sizeof(Beacon)) return;
  Beacon beacon;
  memcpy(&beacon, data, sizeof(beacon));
  if (beacon.magic != 0x3141524F) return;
  int rssi = lastRssi;
  Serial.printf("%lu,%u,%u,%lu,%d\n", (unsigned long)millis(), (unsigned)NODE_ID, (unsigned)beacon.node,
                (unsigned long)beacon.sequence, rssi);
  if (rssi < DROP_THRESHOLD_DBM) {
    // Breadcrumb rule (see ora/breadcrumb.py should_drop): the mover should place the next repeater here.
    Serial.printf("# DROP link to node %u at %d dBm is below %d dBm\n", (unsigned)beacon.node, rssi, DROP_THRESHOLD_DBM);
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_promiscuous_rx_cb(&sniff);
  if (esp_now_init() != ESP_OK) {
    Serial.println("# ESP-NOW init failed");
    return;
  }
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST, 6);
  peer.channel = CHANNEL;
  peer.encrypt = false;
  esp_now_add_peer(&peer);
  esp_now_register_recv_cb(onReceive);
  Serial.println("millis,my_id,from_id,sequence,rssi_dbm");
}

void loop() {
  Beacon beacon = {0x3141524F, (uint8_t)NODE_ID, sequence++};
  esp_now_send(BROADCAST, (const uint8_t *)&beacon, sizeof(beacon));
  delay(BEACON_MS);
}

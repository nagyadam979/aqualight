#!/usr/bin/env python3
"""Exercise production mesh protocol and ESP-NOW adapter with deterministic doubles.

No board, radio, credentials or network access. Tests session-bound commands,
fragmentation/reassembly under loss/reordering, application ACK/idempotence,
deadlines, bounded callbacks/queues and transport callback failure containment.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent

PROTOCOL_CHECKS = r'''
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "mesh_protocol.h"
using namespace boardlink;

void put32(uint8_t* p, uint32_t value) { for (unsigned i=0;i<4;++i) p[i]=value>>(8*i); }
struct Pair {
  Protocol master, slave;
  Pair(uint32_t now=0) {
    assert(master.begin(0x11111111,2,true,now));
    assert(slave.begin(0x22222222,1,false,now));
    Frame f;
    assert(master.prepareFrame(now,f)); master.sent(true); slave.receive(f.data,f.length,now);
    assert(slave.prepareFrame(now,f)); slave.sent(true); master.receive(f.data,f.length,now);
    assert(master.online(now) && slave.online(now));
  }
};
std::vector<Frame> drainState(Protocol& sender,uint32_t start) {
  std::vector<Frame> result;
  for(unsigned i=0;i<8;++i) {
    Frame f;
    if(!sender.prepareFrame(start+i*25,f)) break;
    sender.sent(true);
    if(f.data[5]==static_cast<uint8_t>(Protocol::Kind::State)) result.push_back(f);
  }
  return result;
}
Frame take(Protocol& sender,uint32_t now) {
  Frame f; assert(sender.prepareFrame(now,f));sender.sent(true);return f;
}
void deliver(Protocol& receiver,const Frame& f,uint32_t now) { receiver.receive(f.data,f.length,now); }

void snapshots() {
  Pair pair;
  std::string large(STATE_LIMIT,'x'); large.front()='{';large.back()='}';
  assert(pair.slave.publishState(large.data(),large.size()));
  auto frames=drainState(pair.slave,25);
  assert(frames.size()==5);
  char out[STATE_LIMIT+1];size_t n=0;
  for(size_t i=frames.size();i>1;--i) {
    deliver(pair.master,frames[i-1],100+static_cast<uint32_t>(frames.size()-i));
    assert(!pair.master.receiveState(out,sizeof(out),&n));
  }
  deliver(pair.master,frames[0],110);
  assert(!pair.master.receiveState(out,large.size(),&n)); // Needs space for terminator.
  assert(pair.master.receiveState(out,sizeof(out),&n) && n==large.size());
  assert(std::string(out,n)==large && out[n]==0);
  assert(!pair.master.receiveState(out,sizeof(out),&n));
  deliver(pair.master,frames[0],111); // Same datagram never republishes stale snapshot.
  assert(!pair.master.receiveState(out,sizeof(out),&n));
  assert(pair.master.counters().rxDuplicate==1);
  assert(!pair.slave.publishState(large.data(),STATE_LIMIT+1));
  assert(!pair.slave.publishState("a\0b",3));

  // Missing fragments expire on absolute time, even if other pieces arrive.
  Pair lost;
  assert(lost.slave.publishState(large.data(),large.size()));
  auto partial=drainState(lost.slave,25);
  deliver(lost.master,partial[0],100);
  deliver(lost.master,partial[1],2000);
  lost.master.tick(2300);
  for(size_t i=2;i<partial.size();++i) deliver(lost.master,partial[i],2301+i);
  assert(!lost.master.receiveState(out,sizeof(out),&n));
  assert(lost.master.counters().assemblyExpired==1);
  lost.slave.tick(2400);assert(lost.slave.publishState("{\"new\":true}",12));
  auto fresh=drainState(lost.slave,2400);
  assert(fresh.size()==1);deliver(lost.master,fresh[0],2400);
  assert(lost.master.receiveState(out,sizeof(out),&n) && std::string(out,n)=="{\"new\":true}");

  // A newer generation supersedes partial old state; delayed old pieces cannot
  // complete or overwrite it. Unread complete state is replaced by newest only.
  Pair newest;
  assert(newest.slave.publishState(large.data(),large.size()));
  auto old=drainState(newest.slave,25);
  deliver(newest.master,old[0],100);
  assert(newest.slave.publishState("newest",6));auto latest=drainState(newest.slave,200);
  deliver(newest.master,latest[0],200);
  for(size_t i=1;i<old.size();++i) deliver(newest.master,old[i],201+i);
  assert(newest.master.receiveState(out,sizeof(out),&n) && std::string(out,n)=="newest");
  assert(newest.slave.publishState("one",3));auto one=drainState(newest.slave,300);deliver(newest.master,one[0],300);
  assert(newest.slave.publishState("two",3));auto two=drainState(newest.slave,400);deliver(newest.master,two[0],400);
  assert(newest.master.receiveState(out,sizeof(out),&n) && std::string(out,n)=="two");
}

void commands() {
  Pair pair;
  assert(pair.master.commandStatus(77).phase==CommandPhase::Unknown);
  const uint32_t id=pair.master.queueCommand("/api/mode","mode=manual");assert(id!=0);
  assert(pair.master.queueCommand("/api/mode","mode=off")==0);
  Frame command=take(pair.master,25);deliver(pair.slave,command,25);
  ReceivedCommand incoming;
  assert(pair.slave.receiveCommand(incoming));
  assert(incoming.id==id && std::strcmp(incoming.path,"/api/mode")==0 && std::strcmp(incoming.form,"mode=manual")==0);
  assert(!pair.slave.receiveCommand(incoming));
  assert(pair.master.commandStatus(id).phase==CommandPhase::Pending); // Transport accepted, not applied.
  assert(!pair.slave.replyCommand(id+1,200));
  assert(!pair.slave.replyCommand(id,99));
  assert(pair.slave.replyCommand(id,200,"",42));
  Frame lostAck=take(pair.slave,50); // Lost on air, despite local MAC acceptance.
  assert(pair.master.commandStatus(id).phase==CommandPhase::Pending);
  Frame retry=take(pair.master,425);deliver(pair.slave,retry,425);
  assert(!pair.slave.receiveCommand(incoming)); // No second persist/apply.
  assert(pair.slave.counters().rxCommandDuplicates==1);
  Frame replayedAck=take(pair.slave,450);deliver(pair.master,replayedAck,450);
  const auto applied=pair.master.commandStatus(id);
  assert(applied.phase==CommandPhase::Applied && applied.status==200 && applied.configRevision==42);
  deliver(pair.master,lostAck,451); // Delayed first ACK is harmless.
  assert(pair.master.commandStatus(id).configRevision==42);

  // Fragmented maximum form is not delivered early, and reordered chunks work.
  std::string form(FORM_LIMIT,'a');
  pair.master.tick(500);
  uint32_t longId=pair.master.queueCommand("/api/program",form.c_str());assert(longId);
  assert(pair.master.commandStatus(id).phase==CommandPhase::Applied && pair.master.commandStatus(id).configRevision==42);
  Frame first=take(pair.master,500),second=take(pair.master,525);
  deliver(pair.slave,second,525);assert(!pair.slave.receiveCommand(incoming));
  deliver(pair.slave,first,526);assert(pair.slave.receiveCommand(incoming));
  assert(incoming.id==longId && std::string(incoming.form)==form);
  assert(pair.slave.replyCommand(longId,409,"storage_error",43));
  auto failedAck=take(pair.slave,550);deliver(pair.master,failedAck,550);
  assert(pair.master.commandStatus(longId).phase==CommandPhase::Error);
  assert(pair.master.commandStatus(longId).status==409 && pair.master.commandStatus(longId).configRevision==43);
  assert(std::strcmp(pair.master.commandStatus(longId).error,"storage_error")==0);
  // Newer commands cannot cause an ancient, already completed command to run again.
  command.data[20]+=20;deliver(pair.slave,command,575);assert(!pair.slave.receiveCommand(incoming));

  pair.master.tick(600);
  assert(pair.master.queueCommand("bad","x")==0);
  assert(pair.master.queueCommand(std::string(PATH_LIMIT+1,'x').c_str(),"")==0);
  assert(pair.master.queueCommand("/api/manual",std::string(FORM_LIMIT+1,'x').c_str())==0);
}

void deadlinesAndReboots() {
  Pair noAck;
  auto id=noAck.master.queueCommand("/api/mode","mode=off");assert(id);
  for(uint32_t now=25;now<3000;now+=25) {
    Frame f;
    if(noAck.master.prepareFrame(now,f)) noAck.master.sent(false); // Immediate driver failure.
    assert(noAck.master.commandStatus(id).phase==CommandPhase::Pending);
  }
  noAck.master.tick(3000);
  assert(noAck.master.commandStatus(id).phase==CommandPhase::Timeout);
  assert(noAck.master.commandStatus(id).status==504 && noAck.master.counters().commandTimeouts==1);
  noAck.master.tick(6000);assert(!noAck.master.online(6000));
  assert(noAck.master.queueCommand("/api/mode","mode=off")==0);

  Pair reboot;
  id=reboot.master.queueCommand("/api/mode","mode=off");assert(id);
  auto cmd=take(reboot.master,25);deliver(reboot.slave,cmd,25);
  ReceivedCommand input;assert(reboot.slave.receiveCommand(input));
  assert(reboot.slave.replyCommand(id,200,"",7));auto oldAck=take(reboot.slave,50);
  Protocol newSlave;assert(newSlave.begin(0x33333333,1,false,75));
  auto hello=take(newSlave,75);deliver(reboot.master,hello,75);
  assert(reboot.master.peerBoot()==0x33333333);
  assert(reboot.master.commandStatus(id).phase==CommandPhase::PeerReboot);
  deliver(reboot.master,oldAck,76);
  assert(reboot.master.commandStatus(id).phase==CommandPhase::PeerReboot);
  assert(reboot.master.counters().rxOldSession>=1);
  Protocol newMaster;assert(newMaster.begin(0x44444444,2,true,100));
  deliver(newMaster,oldAck,100); // ACK addressed to previous local boot cannot apply.
  assert(newMaster.peerBoot()==0 && newMaster.counters().rxOldSession==1);

  // Caller may still be waiting for the snapshot matching an applied ACK.
  // Reboot invalidates that ACK revision instead of comparing across boots.
  Pair acknowledged;
  id=acknowledged.master.queueCommand("/api/mode","mode=off");
  auto toApply=take(acknowledged.master,25);deliver(acknowledged.slave,toApply,25);
  assert(acknowledged.slave.receiveCommand(input));assert(acknowledged.slave.replyCommand(id,200,"",91));
  auto appliedAck=take(acknowledged.slave,50);deliver(acknowledged.master,appliedAck,50);
  assert(acknowledged.master.commandStatus(id).phase==CommandPhase::Applied);
  acknowledged.master.tick(60);auto pendingAfterAck=acknowledged.master.queueCommand("/api/mode","mode=manual");assert(pendingAfterAck);
  assert(acknowledged.master.commandStatus(id).phase==CommandPhase::Applied); // Terminal history survives another browser's command.
  Protocol afterAckReboot;assert(afterAckReboot.begin(0x55555555,1,false,75));
  auto newBootHello=take(afterAckReboot,75);deliver(acknowledged.master,newBootHello,75);
  assert(acknowledged.master.commandStatus(id).phase==CommandPhase::PeerReboot);
  assert(acknowledged.master.commandStatus(id).configRevision==0);
  assert(acknowledged.master.commandStatus(pendingAfterAck).phase==CommandPhase::PeerReboot);

  Pair wrapped(UINT32_MAX-1000);
  wrapped.master.tick(UINT32_MAX-500);
  id=wrapped.master.queueCommand("/api/mode","mode=off");assert(id);
  wrapped.master.tick(2498);assert(wrapped.master.commandStatus(id).phase==CommandPhase::Pending);
  wrapped.master.tick(2499);assert(wrapped.master.commandStatus(id).phase==CommandPhase::Timeout);
  assert(wrapped.master.ageMs(100)==1101);

  // Sequence wrap permits new packets and out-of-order delivery around UINT_MAX.
  Protocol seqReceiver;assert(seqReceiver.begin(99,2,true,0));
  Pair source;
  assert(source.slave.publishState("old",3));auto one=drainState(source.slave,25);
  put32(one[0].data+20,UINT32_MAX-1);deliver(seqReceiver,one[0],25);
  assert(source.slave.publishState("new",3));auto two=drainState(source.slave,50);
  put32(two[0].data+20,1);deliver(seqReceiver,two[0],50);
  char text[8];size_t length;
  assert(seqReceiver.receiveState(text,sizeof(text),&length) && std::string(text,length)=="new");
}

void malformedAndBudget() {
  Pair pair;
  assert(pair.master.publishState("queued",6));
  Frame f=take(pair.master,25);
  Frame another;
  assert(!pair.master.prepareFrame(49,another)); // Scheduled gap, no busy waiting.
  assert(!pair.master.prepareFrame(50,another)); // No work until next heartbeat.
  auto baseline=pair.slave.counters().rxInvalid;
  for(size_t n=0;n<HEADER_SIZE;++n) pair.slave.receive(f.data,n,50);
  assert(pair.slave.counters().rxInvalid==baseline+HEADER_SIZE);
  Frame bad=f;bad.data[6]=1;deliver(pair.slave,bad,60);
  bad=f;bad.data[26]=1;deliver(pair.slave,bad,60); // Misaligned fragment.
  bad=f;bad.data[24]=0xff;bad.data[25]=0xff;deliver(pair.slave,bad,60); // Oversized assembly.
  bad=f;bad.data[30]=1;deliver(pair.slave,bad,60); // Wrong peer role/board.
  uint32_t random=0x12345678;
  uint8_t bytes[FRAME_LIMIT+1];
  for(unsigned run=0;run<5000;++run) {
    random^=random<<13;random^=random>>17;random^=random<<5;
    size_t n=random%sizeof(bytes);
    for(size_t i=0;i<n;++i) {random^=random<<13;random^=random>>17;random^=random<<5;bytes[i]=random;}
    pair.slave.receive(bytes,n,70);
  }
  static_assert(sizeof(Protocol)<25000,"Mesh protocol exceeds planned fixed storage");
}

void terminalHistoryAndStaleState() {
  Pair pair;
  uint32_t ids[6];
  for(unsigned i=0;i<6;++i) {
    uint32_t now=25+i*50;pair.master.tick(now);
    ids[i]=pair.master.queueCommand("/api/mode","mode=off");assert(ids[i]);
    auto command=take(pair.master,now);deliver(pair.slave,command,now);
    ReceivedCommand received;assert(pair.slave.receiveCommand(received));
    assert(pair.slave.replyCommand(ids[i],200,"",i+1));
    auto ack=take(pair.slave,now+25);deliver(pair.master,ack,now+25);
    assert(pair.master.commandStatus(ids[i]).phase==CommandPhase::Applied);
  }
  assert(pair.master.commandStatus(ids[0]).phase==CommandPhase::Unknown);
  for(unsigned i=1;i<6;++i)assert(pair.master.commandStatus(ids[i]).configRevision==i+1);
  pair.slave.tick(1000);assert(pair.slave.publishState("expired",7));pair.slave.tick(3200);
  Frame afterPause;
  if(pair.slave.prepareFrame(3200,afterPause)) {
    assert(afterPause.data[5]!=static_cast<uint8_t>(Protocol::Kind::State));pair.slave.sent(true);
  }
  assert(pair.slave.counters().stateExpired==1);
}

int main() {
  snapshots();commands();deadlinesAndReboots();malformedAndBudget();terminalHistoryAndStaleState();
  std::printf("PASS mesh protocol: fixed storage %zu B, newest snapshots, reordering/loss/expiry, command application ACK+revision, idempotent retries, peer/local reboot guards, timeout and rollover\n",sizeof(Protocol));
}
'''

ARDUINO_H = r'''
#pragma once
#include <cstdint>
uint32_t millis();
'''
ESP_SYSTEM_H = r'''
#pragma once
#include <cstdint>
uint32_t esp_random();
'''
FREERTOS_H = r'''
#pragma once
#include <cstddef>
#include <cstdint>
using UBaseType_t=unsigned;
using TickType_t=uint32_t;
using BaseType_t=int;
constexpr int pdTRUE=1,pdFALSE=0;
'''
QUEUE_H = r'''
#pragma once
#include "FreeRTOS.h"
using QueueHandle_t=void*;
QueueHandle_t xQueueCreate(UBaseType_t,size_t);
BaseType_t xQueueSend(QueueHandle_t,const void*,TickType_t);
BaseType_t xQueueReceive(QueueHandle_t,void*,TickType_t);
void vQueueDelete(QueueHandle_t);
BaseType_t xQueueReset(QueueHandle_t);
'''
ESPNOW_H = r'''
#pragma once
#include <cstddef>
#include <cstdint>
using esp_err_t=int;
constexpr esp_err_t ESP_OK=0,ESP_FAIL=-1;
constexpr int WIFI_IF_STA=0;
enum esp_now_send_status_t {ESP_NOW_SEND_SUCCESS,ESP_NOW_SEND_FAIL};
struct wifi_pkt_rx_ctrl_t {uint8_t channel;};
struct esp_now_recv_info_t {const uint8_t* src_addr;const uint8_t* des_addr;wifi_pkt_rx_ctrl_t* rx_ctrl;};
struct esp_now_send_info_t {const uint8_t* des_addr;};
struct esp_now_peer_info_t {uint8_t peer_addr[6],lmk[16];uint8_t channel;int ifidx;bool encrypt;};
using recv_cb_t=void(*)(const esp_now_recv_info_t*,const uint8_t*,int);
using send_cb_t=void(*)(const esp_now_send_info_t*,esp_now_send_status_t);
esp_err_t esp_now_init();
esp_err_t esp_now_deinit();
esp_err_t esp_now_set_pmk(const uint8_t*);
esp_err_t esp_now_add_peer(const esp_now_peer_info_t*);
esp_err_t esp_now_register_recv_cb(recv_cb_t);
esp_err_t esp_now_register_send_cb(send_cb_t);
esp_err_t esp_now_unregister_recv_cb();
esp_err_t esp_now_unregister_send_cb();
esp_err_t esp_now_send(const uint8_t*,const uint8_t*,size_t);
'''

ADAPTER_CHECKS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <deque>
#include <vector>
#include "board_link.h"
#include "esp_now.h"
#include "freertos/queue.h"
using namespace boardlink;
uint32_t fakeNow=0;
uint32_t millis(){return fakeNow;}
uint32_t esp_random(){return 0x12345678;}
struct Queue {unsigned depth;size_t size;std::deque<std::vector<uint8_t>> items;};
Queue* queue=nullptr;
unsigned queueSendCalls=0,queueReceiveCalls=0,initCalls=0,deinitCalls=0,sendCalls=0;
bool pmkOkay=true,sendOkay=true,holdCallback=false,initOkay=true;
recv_cb_t rxCallback=nullptr;send_cb_t txCallback=nullptr;
uint8_t configuredMac[6],configuredPmk[16],configuredLmk[16];
std::vector<Frame> sent;
QueueHandle_t xQueueCreate(UBaseType_t depth,size_t size){assert(!queue);queue=new Queue{depth,size,{}};return queue;}
void vQueueDelete(QueueHandle_t q){assert(q==queue);delete queue;queue=nullptr;}
BaseType_t xQueueReset(QueueHandle_t q){assert(q==queue);queue->items.clear();return pdTRUE;}
BaseType_t xQueueSend(QueueHandle_t q,const void* data,TickType_t wait){
 assert(q==queue && wait==0);++queueSendCalls;
 if(queue->items.size()==queue->depth)return pdFALSE;
 const auto* bytes=static_cast<const uint8_t*>(data);queue->items.emplace_back(bytes,bytes+queue->size);return pdTRUE;
}
BaseType_t xQueueReceive(QueueHandle_t q,void* data,TickType_t wait){
 assert(q==queue && wait==0);++queueReceiveCalls;
 if(queue->items.empty())return pdFALSE;
 std::memcpy(data,queue->items.front().data(),queue->size);queue->items.pop_front();return pdTRUE;
}
esp_err_t esp_now_init(){++initCalls;return initOkay?ESP_OK:ESP_FAIL;}
esp_err_t esp_now_deinit(){++deinitCalls;return ESP_OK;}
esp_err_t esp_now_set_pmk(const uint8_t* key){std::memcpy(configuredPmk,key,16);return pmkOkay?ESP_OK:ESP_FAIL;}
esp_err_t esp_now_add_peer(const esp_now_peer_info_t* peer){
 assert(peer->encrypt && peer->channel==0 && peer->ifidx==WIFI_IF_STA);
 std::memcpy(configuredMac,peer->peer_addr,6);std::memcpy(configuredLmk,peer->lmk,16);return ESP_OK;
}
esp_err_t esp_now_register_recv_cb(recv_cb_t cb){rxCallback=cb;return ESP_OK;}
esp_err_t esp_now_register_send_cb(send_cb_t cb){txCallback=cb;return ESP_OK;}
esp_err_t esp_now_unregister_recv_cb(){rxCallback=nullptr;return ESP_OK;}
esp_err_t esp_now_unregister_send_cb(){txCallback=nullptr;return ESP_OK;}
esp_err_t esp_now_send(const uint8_t* mac,const uint8_t* bytes,size_t length){
 assert(std::memcmp(mac,configuredMac,6)==0 && length<=FRAME_LIMIT);++sendCalls;
 if(!sendOkay)return ESP_FAIL;
 Frame f;f.length=length;std::memcpy(f.data,bytes,length);sent.push_back(f);
 if(!holdCallback)txCallback(nullptr,ESP_NOW_SEND_SUCCESS);
 return ESP_OK;
}
constexpr uint32_t base=1000;
void poll(uint32_t now,bool allowTransmit=true){fakeNow=base+now;unsigned before=sendCalls;tick(fakeNow,allowTransmit);assert(sendCalls<=before+1);}
Frame take(Protocol& peer,uint32_t now){Frame f;assert(peer.prepareFrame(base+now,f));peer.sent(true);return f;}
void inject(const Frame& frame,uint8_t channel=4,const uint8_t* mac=nullptr){
 wifi_pkt_rx_ctrl_t control{channel};esp_now_recv_info_t info{mac?mac:configuredMac,nullptr,&control};
 rxCallback(&info,frame.data,static_cast<int>(frame.length));
}

int main(){
 Config config;config.master=true;config.channels=2;
 const uint8_t peerMac[6]={2,4,6,8,10,12};std::memcpy(config.peerMac,peerMac,6);
 for(unsigned i=0;i<16;++i){config.pmk[i]=i+1;config.lmk[i]=i+17;}
 Config invalid=config;invalid.peerMac[0]=3;assert(!begin(invalid) && initCalls==0);
 pmkOkay=false;assert(!begin(config) && initCalls==1 && deinitCalls==1 && queue && !available());
 pmkOkay=true;fakeNow=999;tick(fakeNow);assert(initCalls==1);
 fakeNow=1000;tick(fakeNow,false);assert(initCalls==1 && deinitCalls==1 && !available());
 fakeNow=1000;tick(fakeNow);assert(initCalls==2);
 assert(begin(config) && available());assert(queue->depth==4 && counters().radioRecoveries==1);
 assert(std::memcmp(config.pmk,configuredPmk,16)==0 && std::memcmp(config.lmk,configuredLmk,16)==0);
 assert(localBootId()==0x12345678 && !online(0));
 poll(0);assert(sent.size()==1);
 Protocol peer;assert(peer.begin(0x87654321,1,false,base));peer.receive(sent.back().data,sent.back().length,base);
 auto hello=take(peer,0);inject(hello);poll(25);
 assert(online(base+25) && peerBootId()==0x87654321 && lastPeerChannel()==4);
 fakeNow=base+30;uint32_t id=queueCommand("/api/mode","mode=manual");assert(id);
 poll(50);assert(sent.back().data[5]==static_cast<uint8_t>(Protocol::Kind::Command));
 assert(commandStatus(id).phase==CommandPhase::Pending && counters().txMacAcked>=2);
 peer.receive(sent.back().data,sent.back().length,base+50);
 ReceivedCommand received;assert(peer.receiveCommand(received));assert(peer.replyCommand(id,200,"",77));
 inject(take(peer,50));poll(75);
 assert(commandStatus(id).phase==CommandPhase::Applied && commandStatus(id).configRevision==77);

 // RX callback accepts only the configured peer and bounded frame length;
 // full queue drops immediately and tick drains at most two frames.
 unsigned attempts=queueSendCalls;uint8_t wrongMac[6]={2,4,6,8,10,99};inject(hello,4,wrongMac);
 Frame shortFrame;shortFrame.length=1;inject(shortFrame);assert(queueSendCalls==attempts);
 for(unsigned i=0;i<5;++i){assert(peer.publishState("{}",2));inject(take(peer,100+i*25));}
 assert(queue->items.size()==4 && counters().rxQueueDrops==1);
 auto before=counters().rxFrames;poll(100);
 assert(queue->items.size()==2 && counters().rxFrames==before+2);
 poll(125);assert(queue->items.empty());
 char state[16];size_t size=0;assert(receiveState(state,sizeof(state),&size) && size==2);

 // Unvalidated bytes cannot overwrite the acquisition channel following a
 // valid packet in the same tick timestamp.
 assert(peer.publishState("{}",2));auto valid=take(peer,250);inject(valid,6);
 auto bad=valid;bad.data[0]='?';inject(bad,12);poll(150);assert(lastPeerChannel()==6);
 // Main-loop timestamp may be older than a just-arrived Wi-Fi callback.
 peer.tick(base+275);assert(peer.publishState("clock",5));auto clockFrame=take(peer,275);
 fakeNow=base+151;inject(clockFrame,7);before=counters().rxFrames;tick(base+150);
 assert(counters().rxFrames==before+1 && lastPeerChannel()==7);

 // Missing MAC callbacks trigger finite driver retirement/recreation instead
 // of a permanently paused sender. The command deadline remains absolute.
 auto priorGenerationCallback=txCallback;
 holdCallback=true;assert(publishState("held",4));poll(175);
 unsigned heldSends=sendCalls;fakeNow=base+200;id=queueCommand("/api/mode","mode=off");assert(id);
 for(uint32_t now=200;now<=3200;now+=100)poll(now);
 assert(sendCalls==heldSends+3 && commandStatus(id).phase==CommandPhase::Timeout);
 assert(counters().txCallbackTimeouts==3 && counters().radioRecoveries==4);
 assert(txCallback!=priorGenerationCallback);
 priorGenerationCallback(nullptr,ESP_NOW_SEND_SUCCESS); // Cannot release the new send.
 poll(3225);assert(sendCalls==heldSends+3);
 before=counters().rxFrames;assert(peer.publishState("alive",5));inject(take(peer,3250));poll(3225);
 assert(counters().rxFrames==before+1 && online(base+3225));
 holdCallback=false;txCallback(nullptr,ESP_NOW_SEND_FAIL);poll(3250);
 assert(sendCalls==heldSends+4 && counters().txMacFailed==1);

 // An immediate driver rejection is one attempt per poll, paced rather than
 // retried in a delay loop. Unknown command IDs remain safe.
 sendOkay=false;assert(publishState("reject",6));unsigned beforeRejected=counters().txRejected;
 poll(3275);unsigned at3275=sendCalls;poll(3276);assert(sendCalls==at3275);
 poll(3300);assert(sendCalls==at3275+1 && counters().txRejected==beforeRejected+2);
 assert(commandStatus(9999).phase==CommandPhase::Unknown);

 // Failed driver recovery backs off 1,2,4,... seconds without tight init/send
 // loops; a later successful init resumes independently of peer presence.
 sendOkay=true;holdCallback=true;assert(publishState("driver",6));poll(3325);
 poll(3825);assert(!online(base+3825) && !available());
 assert(publishState("during-recovery",15)); // Latest state may replace while driver is unavailable.
 initOkay=false;unsigned initBefore=initCalls;poll(4075);assert(initCalls==initBefore+1);
 poll(5074);assert(initCalls==initBefore+1);
 poll(5075);assert(initCalls==initBefore+2);
 poll(7074);assert(initCalls==initBefore+2);
 poll(7075);assert(initCalls==initBefore+3);
 assert(counters().radioFailures==4); // Initial PMK failure + three recovery failures.
 initOkay=true;holdCallback=false;poll(11075);assert(initCalls==initBefore+4);
 assert(counters().radioRecoveries==5 && available());
 assert(publishState("fresh-after-recovery",20));
 poll(11100);assert(sent.back().length==HEADER_SIZE+20);
 assert(std::memcmp(sent.back().data+HEADER_SIZE,"fresh-after-recovery",20)==0);
 peer.tick(base+11200);assert(peer.publishState("stale",5));inject(take(peer,11200));
 auto staleBefore=counters().rxStaleFrames;poll(13400);
 assert(counters().rxStaleFrames==staleBefore+1); // Callback time, not processing time, controls expiry.

 // A 15 s exclusive STA interval keeps RX and application deadlines alive,
 // without sending or touching the ESP-NOW driver even with a pending MAC ACK.
 fakeNow=base+13425;peer.tick(fakeNow);assert(peer.publishState("before-pause",12));
 inject(take(peer,13425));poll(13425,false);assert(online(fakeNow));
 id=queueCommand("/api/mode","mode=off");assert(id);
 holdCallback=true;poll(13450);assert(commandStatus(id).phase==CommandPhase::Pending);
 const unsigned pausedSends=sendCalls,pausedInits=initCalls,pausedDeinits=deinitCalls;
 const auto pausedTimeouts=counters().txCallbackTimeouts;
 poll(13451,false);
 poll(16425,false);assert(commandStatus(id).phase==CommandPhase::Timeout);
 fakeNow=base+20000;peer.tick(fakeNow);assert(peer.publishState("during-pause",12));
 inject(take(peer,20000));before=counters().rxFrames;poll(20000,false);
 assert(counters().rxFrames==before+1 && online(fakeNow));
 assert(receiveState(state,sizeof(state),&size) && std::memcmp(state,"during-pause",12)==0);
 poll(28451,false);
 assert(sendCalls==pausedSends && initCalls==pausedInits && deinitCalls==pausedDeinits);
 assert(counters().txCallbackTimeouts==pausedTimeouts && available());
 txCallback(nullptr,ESP_NOW_SEND_SUCCESS); // A valid callback may finish during the pause.
 poll(28475,false);assert(sendCalls==pausedSends);
 poll(28500);assert(sendCalls==pausedSends+1); // Resume starts one new held send.

 // A genuinely lost callback gets a fresh grace interval at resume, followed
 // by exactly one retirement and the existing 250 ms recovery cooldown.
 poll(28501,false);poll(43501,false);
 assert(deinitCalls==pausedDeinits && sendCalls==pausedSends+1);
 poll(43502);poll(44001);
 assert(deinitCalls==pausedDeinits && sendCalls==pausedSends+1);
 // A callback arriving inside the fresh grace period releases the send.
 txCallback(nullptr,ESP_NOW_SEND_SUCCESS);poll(44002);
 assert(deinitCalls==pausedDeinits && sendCalls==pausedSends+2);
 poll(44003,false);poll(59003,false);poll(59004);poll(59503);
 assert(deinitCalls==pausedDeinits);
 poll(59504);assert(deinitCalls==pausedDeinits+1 && !available());
 poll(59753);assert(initCalls==pausedInits);
 poll(59754,false);poll(60000,false);assert(initCalls==pausedInits); // Recovery itself is paused.
 poll(60001);assert(initCalls==pausedInits+1 && available());
 poll(60026);assert(sendCalls==pausedSends+3);

 // Both the resumed callback watchdog and scheduled recovery remain safe
 // across millis() rollover. No stale suspension duration becomes a timeout.
 txCallback(nullptr,ESP_NOW_SEND_SUCCESS);
 fakeNow=UINT32_MAX-1200;assert(publishState("wrap",4));tick(fakeNow);
 unsigned wrapDeinits=deinitCalls,wrapInits=initCalls;
 fakeNow=UINT32_MAX-1100;tick(fakeNow,false);
 fakeNow=UINT32_MAX-600;tick(fakeNow);
 fakeNow=UINT32_MAX-101;tick(fakeNow);assert(deinitCalls==wrapDeinits);
 fakeNow=UINT32_MAX-100;tick(fakeNow);assert(deinitCalls==wrapDeinits+1);
 fakeNow=148;tick(fakeNow);assert(initCalls==wrapInits);
 fakeNow=149;tick(fakeNow);assert(initCalls==wrapInits+1);
 fakeNow=UINT32_MAX-300;assert(publishState("wrap-again",10));tick(fakeNow);
 fakeNow=UINT32_MAX-200;tick(fakeNow,false);
 fakeNow=UINT32_MAX-50;tick(fakeNow); // Resume before wrap.
 fakeNow=448;tick(fakeNow);assert(deinitCalls==wrapDeinits+1);
 fakeNow=449;tick(fakeNow);assert(deinitCalls==wrapDeinits+2);
 fakeNow=698;tick(fakeNow);assert(initCalls==wrapInits+1);
 fakeNow=699;tick(fakeNow);assert(initCalls==wrapInits+2);
 std::puts("PASS ESP-NOW adapter: encrypted config, bounded queues/RX/TX, application ACK, callback recovery/backoff, exclusive 15 s STA pause with live RX/deadlines, late callbacks, fresh resume grace and millis rollover");
 delete queue;queue=nullptr;
}
'''


def compile_and_run(name, source, temp, extra_sources=(), include_dirs=()):
    cpp, binary = temp / (name + ".cpp"), temp / name
    cpp.write_text(source)
    args = [os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "src")]
    for directory in include_dirs:
        args.extend(["-I", str(directory)])
    subprocess.run(args + [str(cpp)] + [str(path) for path in extra_sources] + ["-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


with tempfile.TemporaryDirectory(prefix="aqualight-board-link-") as directory:
    temp = Path(directory)
    compile_and_run("protocol", PROTOCOL_CHECKS, temp)
    includes = temp / "include"
    (includes / "freertos").mkdir(parents=True)
    for name, body in {"Arduino.h": ARDUINO_H, "esp_system.h": ESP_SYSTEM_H,
                       "esp_now.h": ESPNOW_H, "freertos/FreeRTOS.h": FREERTOS_H,
                       "freertos/queue.h": QUEUE_H}.items():
        (includes / name).write_text(body)
    compile_and_run("adapter", ADAPTER_CHECKS, temp, [ROOT / "src/board_link.cpp"], [includes])

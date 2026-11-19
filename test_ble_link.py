#!/usr/bin/env python3
"""Production BLE protocol + adapter, deterministic NimBLE/FreeRTOS doubles.

No radio, PINs, network or device access. Compile both with ASan/UBSan.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
COMMON = r'''
#include "ble_protocol.h"
#include <cassert>
#include <cstring>
#include <string>
#include <vector>
using namespace blelink;
Frame request(uint16_t id,const std::string& body,size_t offset=0,size_t amount=170) {
 Frame f;size_t n=std::min(amount,body.size()-offset);f.length=HEADER_SIZE+n;
 f.data[0]=1;f.data[1]=1;write16(f.data+2,id);write16(f.data+4,offset);
 write16(f.data+6,body.size());write16(f.data+8,n);std::memcpy(f.data+10,body.data()+offset,n);return f;
}
'''
PROTOCOL = COMMON + r'''
#include <cstdio>
int main(){
 Protocol p;p.reset(1);Frame out;char text[REQUEST_LIMIT+1];size_t length;uint16_t id;
 std::string large(REQUEST_LIMIT,'x');
 auto first=request(1,large);assert(p.receive(first.data,first.length,0));
 auto reorder=request(1,large,340);assert(!p.receive(reorder.data,reorder.length,1));
 assert(!p.receiveRequest(text,sizeof(text),&length,&id));
 assert(!p.receive(first.data,first.length,2)); // Duplicate offset never appends.
 for(size_t off=170;off<large.size();off+=170){auto f=request(1,large,off);assert(p.receive(f.data,f.length,3));}
 assert(!p.receiveRequest(text,large.size(),&length,&id)); // Terminator needs one byte.
 assert(p.receiveRequest(text,sizeof(text),&length,&id)&&id==1&&length==large.size()&&text[length]==0);
 assert(std::string(text,length)==large&&!p.receiveRequest(text,sizeof(text),&length,&id));
 assert(!p.reply(2,"{}",2,4)&&!p.reply(1,"",0,4));
 std::string response(RESPONSE_LIMIT,'y');assert(!p.reply(1,response.data(),response.size()+1,4));
 assert(p.reply(1,response.data(),response.size(),4));response.assign(response.size(),'z'); // Copy lifetime.
 assert(!p.receive(request(2,"{}").data,12,5)); // No request backlog during response.
 std::string reconstructed;uint32_t now=5;
 while(p.prepare(out,20,now)){
  assert(out.length<=20&&out.data[1]==2&&read16(out.data+2)==1&&read16(out.data+4)==reconstructed.size());
  assert(read16(out.data+6)==RESPONSE_LIMIT);
  if(reconstructed.empty()) {p.sent(false,now);assert(!p.prepare(out,20,now+19));now+=20;assert(p.prepare(out,20,now));}
  reconstructed.append(reinterpret_cast<const char*>(out.data+10),out.length-10);p.sent(true,now);now+=5;
 }
 assert(reconstructed==std::string(RESPONSE_LIMIT,'y')&&p.counters().txRejected==1);
 assert(!p.receive(first.data,first.length,now)); // Completed ID cannot execute again.
 auto second=request(2,"{}");assert(p.receive(second.data,second.length,now));
 assert(p.receiveRequest(text,sizeof(text),&length,&id)&&id==2);
 p.tick(now+APPLICATION_MS);assert(!p.reply(2,"{}",2,now+APPLICATION_MS));
 assert(p.counters().applicationTimeouts==1&&!p.receive(second.data,second.length,now+APPLICATION_MS));

 Protocol timeout;timeout.reset(2);assert(timeout.receive(first.data,first.length,UINT32_MAX-2000));
 auto next=request(1,large,170);assert(timeout.receive(next.data,next.length,UINT32_MAX-1));
 timeout.tick(12998);assert(timeout.counters().assemblyTimeouts==0);
 timeout.tick(12999);assert(timeout.counters().assemblyTimeouts==1);
 assert(!timeout.receive(first.data,first.length,13000)); // Same ID reserved even on expiry.
 auto fresh=request(2,"{}");assert(timeout.receive(fresh.data,fresh.length,13000));
 assert(timeout.receiveRequest(text,sizeof(text),&length,&id));
 timeout.reset(3);assert(timeout.receive(fresh.data,fresh.length,UINT32_MAX-10));
 assert(timeout.receiveRequest(text,sizeof(text),&length,&id));assert(timeout.reply(id,"{}",2,UINT32_MAX-10));
 timeout.tick(29988);assert(timeout.counters().responseTimeouts==0);
 timeout.tick(29989);assert(timeout.counters().responseTimeouts==1&&!timeout.prepare(out,180,29989));
 timeout.reset(3);assert(timeout.receive(first.data,first.length,0)); // New connection restarts IDs.
 timeout.reset(4);assert(!timeout.receiveRequest(text,sizeof(text),&length,&id)&&!timeout.reply(1,"{}",2,0));
 auto last=request(65535,"{}");assert(timeout.receive(last.data,last.length,0));
 assert(timeout.receiveRequest(text,sizeof(text),&length,&id)&&id==65535);
 assert(timeout.reply(id,"{}",2,0)&&timeout.prepare(out,180,0));timeout.sent(true,0);
 auto wrappedId=request(1,"{}");assert(!timeout.receive(wrappedId.data,wrappedId.length,1));
 timeout.reset(5);assert(timeout.receive(wrappedId.data,wrappedId.length,2));

 Protocol invalid;invalid.reset(1);
 for(size_t n=0;n<HEADER_SIZE;++n)assert(!invalid.receive(first.data,n,0));
 for(unsigned field: {0u,1u,2u,8u}){auto bad=first;bad.data[field]=0;assert(!invalid.receive(bad.data,bad.length,0));}
 auto bad=first;write16(bad.data+6,REQUEST_LIMIT+1);assert(!invalid.receive(bad.data,bad.length,0));
 bad=first;write16(bad.data+4,65535);assert(!invalid.receive(bad.data,bad.length,0));
 bad=first;write16(bad.data+8,65535);assert(!invalid.receive(bad.data,bad.length,0));
 bad=first;bad.data[HEADER_SIZE]=0;assert(!invalid.receive(bad.data,bad.length,0));
 assert(!invalid.receive(first.data,first.length-1,0));
 uint32_t random=123;uint8_t fuzz[FRAME_LIMIT+1];
 for(unsigned run=0;run<5000;++run){random=random*1664525+1013904223;size_t n=random%sizeof(fuzz);
  for(size_t i=0;i<n;++i){random=random*1664525+1013904223;fuzz[i]=random>>24;}invalid.receive(fuzz,n,run);}
 static_assert(sizeof(Protocol)<9000,"BLE protocol storage grew");
 std::printf("PASS BLE protocol: %zu B fixed storage, contiguous bounded frames, rejection/fuzz, no replay, lifecycle/deadlines/wrap, backpressure copy lifetime\n",sizeof(Protocol));
}
'''
NIMBLE = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <string>
#include <vector>
#include <cassert>
constexpr int BLE_HS_IO_DISPLAY_ONLY=1;
struct BleHsConfig {unsigned sm_sc_only:1;};inline BleHsConfig ble_hs_cfg{};
namespace NIMBLE_PROPERTY {enum {READ=1,READ_ENC=2,READ_AUTHEN=4,WRITE=8,WRITE_ENC=16,WRITE_AUTHEN=32,NOTIFY=64};}
struct NimBLEConnInfo {
 uint16_t handle=7,mtu=23;bool encrypted=true,authenticated=true;uint8_t keySize=16;
 uint16_t getConnHandle()const{return handle;}uint16_t getMTU()const{return mtu;}
 bool isEncrypted()const{return encrypted;}bool isAuthenticated()const{return authenticated;}
 uint8_t getSecKeySize()const{return keySize;}
};
class NimBLECharacteristic;class NimBLEServer;class NimBLEService;
class NimBLECharacteristicCallbacks {public:virtual ~NimBLECharacteristicCallbacks()=default;
 virtual void onRead(NimBLECharacteristic*,NimBLEConnInfo&){}
 virtual void onSubscribe(NimBLECharacteristic*,NimBLEConnInfo&,uint16_t){}
};
class NimBLEServerCallbacks {public:virtual ~NimBLEServerCallbacks()=default;
 virtual void onConnect(NimBLEServer*,NimBLEConnInfo&){}virtual void onDisconnect(NimBLEServer*,NimBLEConnInfo&,int){}
 virtual void onMTUChange(uint16_t,NimBLEConnInfo&){}virtual void onAuthenticationComplete(NimBLEConnInfo&){}
};
inline bool notifyOkay=true;inline unsigned notifyCalls=0;
inline std::vector<std::vector<uint8_t>> notifications;
class NimBLECharacteristic {
public:std::string uuid,value;uint16_t properties,maxLen;NimBLECharacteristicCallbacks* callbacks=nullptr;
 NimBLECharacteristic(const char*u,uint16_t p,uint16_t max,NimBLEService* =nullptr):uuid(u),properties(p),maxLen(max){}
 virtual ~NimBLECharacteristic()=default;
 void setValue(const char*s){value=s;}void setValue(const uint8_t*p,size_t n){assert(n<=maxLen);value.assign(reinterpret_cast<const char*>(p),n);}
 void setCallbacks(NimBLECharacteristicCallbacks*c){callbacks=c;}
 bool notify(const uint8_t*p,size_t n,uint16_t handle){assert(handle!=0xffff);++notifyCalls;
  if(!notifyOkay)return false;notifications.emplace_back(p,p+n);return true;}
 void write(const uint8_t*p,uint16_t n,NimBLEConnInfo&i){writeEvent(p,n,i);}
protected:virtual void writeEvent(const uint8_t*,uint16_t,NimBLEConnInfo&){}
};
class NimBLEService {public:std::vector<NimBLECharacteristic*> chars;
 ~NimBLEService(){for(auto*c:chars)delete c;}
 void addCharacteristic(NimBLECharacteristic*c){chars.push_back(c);}
 NimBLECharacteristic*createCharacteristic(const char*u,uint32_t p,uint16_t max){auto*c=new NimBLECharacteristic(u,p,max);addCharacteristic(c);return c;}
};
class NimBLEServer {public:NimBLEServerCallbacks* callbacks=nullptr;NimBLEService*service=nullptr;unsigned disconnections=0;NimBLEConnInfo peerInfo;bool disconnectOkay=true;
 ~NimBLEServer(){delete service;}
 void setCallbacks(NimBLEServerCallbacks*c,bool own){assert(!own);callbacks=c;}
 void advertiseOnDisconnect(bool b){assert(b);}
 NimBLEService*createService(const char*){service=new NimBLEService;return service;}
 bool start(){return true;}bool disconnect(uint16_t){++disconnections;return disconnectOkay;}
 NimBLEConnInfo getPeerInfoByHandle(uint16_t)const{return peerInfo;}
};
class NimBLEAdvertising {public:bool started=false;
 void enableScanResponse(bool b){assert(b);}bool addServiceUUID(const char*){return true;}
 bool setName(const char*){return true;}bool start(){started=true;return true;}
};
class NimBLEDevice {public:
 inline static NimBLEServer* server=nullptr;inline static NimBLEAdvertising adv;
 inline static bool failInit=false,bonding=false,mitm=false,sc=false;inline static uint32_t passkey=0;
 static bool init(const std::string&){return !failInit;}static bool deinit(bool){delete server;server=nullptr;return true;}
 static void setSecurityAuth(bool b,bool m,bool s){bonding=b;mitm=m;sc=s;}
 static void setSecurityPasskey(uint32_t p){passkey=p;}static void setSecurityIOCap(int i){assert(i==BLE_HS_IO_DISPLAY_ONLY);}
 static bool setMTU(uint16_t mtu){assert(mtu==183);return true;}
 static NimBLEServer*createServer(){server=new NimBLEServer;return server;}
 static NimBLEAdvertising*getAdvertising(){return &adv;}
};
'''
FREERTOS = r'''
#pragma once
#include <cstdint>
#include <cstddef>
using UBaseType_t=unsigned;using BaseType_t=int;using TickType_t=uint32_t;
constexpr int pdTRUE=1,pdFALSE=0;
'''
QUEUE = r'''
#pragma once
#include "FreeRTOS.h"
using QueueHandle_t=void*;
QueueHandle_t xQueueCreate(UBaseType_t,size_t);
void vQueueDelete(QueueHandle_t);
BaseType_t xQueueSend(QueueHandle_t,const void*,TickType_t);
BaseType_t xQueueReceive(QueueHandle_t,void*,TickType_t);
'''
ADAPTER = COMMON + r'''
#include "ble_link.h"
#include "NimBLEDevice.h"
#include "freertos/queue.h"
#include <deque>
#include <cstdio>
uint32_t now=0;uint32_t millis(){return now;}
struct Queue {unsigned cap;size_t size;std::deque<std::vector<uint8_t>> items;};
Queue*queue=nullptr;
QueueHandle_t xQueueCreate(UBaseType_t n,size_t size){assert(!queue);queue=new Queue{n,size,{}};return queue;}
void vQueueDelete(QueueHandle_t p){assert(p==queue);delete queue;queue=nullptr;}
BaseType_t xQueueSend(QueueHandle_t p,const void*data,TickType_t wait){assert(p==queue&&wait==0);
 if(queue->items.size()==queue->cap)return pdFALSE;const auto*bytes=static_cast<const uint8_t*>(data);
 queue->items.emplace_back(bytes,bytes+queue->size);return pdTRUE;}
BaseType_t xQueueReceive(QueueHandle_t p,void*data,TickType_t wait){assert(p==queue&&wait==0);
 if(queue->items.empty())return pdFALSE;std::memcpy(data,queue->items.front().data(),queue->size);queue->items.pop_front();return pdTRUE;}
void poll(uint32_t at){now=at;unsigned before=notifyCalls;tick(now);assert(notifyCalls<=before+1);}
int main(){
 Config c;c.channels=2;c.name="AquaLight-Test";c.passkey=654321;c.version="1.4.0-ble";
 Config bad=c;bad.passkey=1;assert(!begin(bad));bad=c;bad.version="bad\"json";assert(!begin(bad));
 NimBLEDevice::failInit=true;assert(!begin(c)&&!ready()&&!queue);NimBLEDevice::failInit=false;
 assert(begin(c)&&ready()&&!connected()&&queue->cap==4);
 assert(NimBLEDevice::bonding&&NimBLEDevice::mitm&&NimBLEDevice::sc&&ble_hs_cfg.sm_sc_only);
 assert(NimBLEDevice::passkey==c.passkey&&NimBLEDevice::adv.started);
 auto*s=NimBLEDevice::server;auto*rx=s->service->chars[0];auto*tx=s->service->chars[1];auto*info=s->service->chars[2];
 assert(rx->properties==(NIMBLE_PROPERTY::WRITE|NIMBLE_PROPERTY::WRITE_ENC|NIMBLE_PROPERTY::WRITE_AUTHEN));
 assert((tx->properties&NIMBLE_PROPERTY::READ_AUTHEN)&&tx->value.empty());
 NimBLEConnInfo peer;peer.encrypted=false;peer.authenticated=false;s->callbacks->onConnect(s,peer);poll(0);
 assert(connected());auto one=request(1,"{}");rx->write(one.data,one.length,peer);assert(queue->items.empty());
 peer.encrypted=true;peer.authenticated=true;s->callbacks->onAuthenticationComplete(peer);
 tx->callbacks->onSubscribe(tx,peer,1);info->callbacks->onRead(info,peer);
 assert(info->value.find("\"maxFrame\":20")!=std::string::npos&&info->value.find("1.4.0-ble")!=std::string::npos);
 rx->write(one.data,one.length,peer);poll(1);char text[REQUEST_LIMIT+1];size_t n;uint16_t id;
 assert(receiveRequest(text,sizeof(text),&n,&id)&&id==1&&n==2);
 assert(reply(id,"{\"status\":200,\"body\":{}}",24));
 notifyOkay=false;poll(2);assert(notifyCalls==1&&notifications.empty());
 poll(21);assert(notifyCalls==1);notifyOkay=true;poll(22);assert(notifications.size()==1&&notifications.back().size()==20);
 poll(27);poll(32);assert(notifications.size()==3&&tx->value.empty()); // Read never exposes response.
 rx->write(one.data,one.length,peer);poll(40);assert(!receiveRequest(text,sizeof(text),&n,&id)&&counters().rxReplay==1);

 // At the default MTU23/20-byte frame, 2048 bytes need 205 writes. A realistic
 // 30 ms acknowledged write + 5 ms client pacing exceeds 5 s but fits 15 s.
 std::string maximum(REQUEST_LIMIT,'m');
 for(size_t offset=0;offset<maximum.size();offset+=10){
  now=100+static_cast<uint32_t>(offset/10)*35;auto f=request(2,maximum,offset,10);
  assert(f.length<=20);rx->write(f.data,f.length,peer);poll(now);
  if(offset+10<maximum.size())assert(!receiveRequest(text,sizeof(text),&n,&id));
 }
 assert(now>5000&&now<ASSEMBLY_MS&&receiveRequest(text,sizeof(text),&n,&id));
 assert(id==2&&n==REQUEST_LIMIT&&std::string(text,n)==maximum&&counters().assemblyTimeouts==0);
 assert(reply(id,"{}",2));poll(now+5);

 // Negotiated MTU cap, queue overflow and at most two frames drained per poll.
 peer.mtu=247;s->callbacks->onMTUChange(peer.mtu,peer);info->callbacks->onRead(info,peer);
 assert(info->value.find("\"maxFrame\":180")!=std::string::npos);
 std::string big(REQUEST_LIMIT,'a');now=8000;
 for(unsigned i=0;i<5;++i){auto f=request(3,big,i*170);rx->write(f.data,f.length,peer);}
 assert(queue->items.size()==4&&counters().rxQueueDrops==1);poll(8001);assert(queue->items.size()==2);
 poll(8002);assert(queue->items.empty()); // Missing dropped piece must expire, never execute.
 poll(22999);assert(counters().assemblyTimeouts==0);
 poll(23000);assert(counters().assemblyTimeouts==1&&!receiveRequest(text,sizeof(text),&n,&id));
 auto four=request(4,"{}");now=23100;rx->write(four.data,four.length,peer);poll(23100);
 assert(receiveRequest(text,sizeof(text),&n,&id)&&id==4);
 // No reply can cross disconnect/reconnect, including recycled handles/IDs.
 s->callbacks->onDisconnect(s,peer,0);s->callbacks->onConnect(s,peer);
 assert(!reply(4,"{}",2));s->callbacks->onAuthenticationComplete(peer);tx->callbacks->onSubscribe(tx,peer,1);
 now=23101;rx->write(one.data,one.length,peer);poll(23101);assert(receiveRequest(text,sizeof(text),&n,&id)&&id==1);
 assert(reply(id,std::string(RESPONSE_LIMIT,'r').c_str(),RESPONSE_LIMIT));
 poll(23102);assert(notifications.back().size()==180); // Actual MTU-sized emission, not only INFO.
 notifyOkay=false;unsigned stalled=notifyCalls;
 for(uint32_t t=23122;t<53101;t+=20)poll(t);
 poll(53101);assert(counters().responseTimeouts==1&&notifyCalls<=stalled+1500);
 stalled=notifyCalls;poll(58000);assert(notifyCalls==stalled); // Finite retry deadline.

 // Old queued frames are generation-bound, not trusted after handle reuse.
 now=58001;auto two=request(2,"{}");rx->write(two.data,two.length,peer);
 s->callbacks->onDisconnect(s,peer,0);s->callbacks->onConnect(s,peer);poll(58002);
 assert(!receiveRequest(text,sizeof(text),&n,&id)&&counters().rxStale>=1);
 s->callbacks->onAuthenticationComplete(peer);tx->callbacks->onSubscribe(tx,peer,1);
 now=58003;rx->write(one.data,one.length,peer);poll(73003);assert(!receiveRequest(text,sizeof(text),&n,&id)); // Queue age.
 now=73004;rx->write(one.data,one.length,peer);poll(73004);assert(receiveRequest(text,sizeof(text),&n,&id));
 // A cached authenticated/subscribe callback never overrides fresh host data.
 notifyOkay=true;assert(reply(id,"{}",2));unsigned beforeSecurity=notifyCalls;
 s->peerInfo.encrypted=false;poll(73005);assert(notifyCalls==beforeSecurity);
 s->peerInfo.encrypted=true;s->peerInfo.authenticated=false;poll(73010);assert(notifyCalls==beforeSecurity);
 s->peerInfo.authenticated=true;s->peerInfo.keySize=7;poll(73015);assert(notifyCalls==beforeSecurity);
 s->peerInfo.keySize=16;s->peerInfo.handle=99;poll(73020);assert(notifyCalls==beforeSecurity);
 s->peerInfo=peer;poll(73025);assert(notifyCalls==beforeSecurity+1);
 s->callbacks->onDisconnect(s,peer,0);assert(!reply(id,"{}",2)&&!connected());
 assert(counters().connections==3&&counters().disconnects==3);

 // Unpaired clients cannot hold the single connection forever. The 45 s
 // deadline issues one owner-task disconnect, not a tight disconnect loop.
 peer.encrypted=false;peer.authenticated=false;s->peerInfo=peer;
 now=80000;s->callbacks->onConnect(s,peer);unsigned disconnectBefore=s->disconnections;
 poll(124999);assert(s->disconnections==disconnectBefore);
 poll(125000);assert(s->disconnections==disconnectBefore+1);
 poll(125001);assert(s->disconnections==disconnectBefore+1);
 s->callbacks->onDisconnect(s,peer,0);

 // Legitimate pairing completed just before the deadline keeps its slot.
 now=200000;s->callbacks->onConnect(s,peer);poll(244999);
 peer.encrypted=true;peer.authenticated=true;s->peerInfo=peer;
 s->callbacks->onAuthenticationComplete(peer);poll(245000);poll(300000);
 assert(s->disconnections==disconnectBefore+1);s->callbacks->onDisconnect(s,peer,0);

 // Authentication deadline also works across millis wrap.
 peer.encrypted=false;peer.authenticated=false;s->peerInfo=peer;
 now=UINT32_MAX-20000;s->callbacks->onConnect(s,peer);
 poll(24998);assert(s->disconnections==disconnectBefore+1);
 poll(24999);assert(s->disconnections==disconnectBefore+2);
 poll(25000);assert(s->disconnections==disconnectBefore+2);
 s->callbacks->onDisconnect(s,peer,0);
 // A transient driver rejection retries at 1 s spacing, never every tick.
 now=100000;s->callbacks->onConnect(s,peer);s->disconnectOkay=false;
 poll(145000);assert(s->disconnections==disconnectBefore+3);
 poll(145999);assert(s->disconnections==disconnectBefore+3);
 s->disconnectOkay=true;poll(146000);assert(s->disconnections==disconnectBefore+4);
 poll(147000);assert(s->disconnections==disconnectBefore+4);
 s->callbacks->onDisconnect(s,peer,0);
 NimBLEDevice::deinit(true);vQueueDelete(queue);
 std::puts("PASS BLE adapter: security/SC/PIN, fresh TX security, 45 s pairing deadline/wrap, MTU/INFO, paced maximum request, bounded queue/RX/TX, finite backpressure and connection generation guards");
}
'''


def run(name, source, temp, extra=()):
    cpp = temp / (name + '.cpp')
    binary = temp / name
    cpp.write_text(source)
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-DLED_BLE_ONLY=1',
                    '-I', str(ROOT / 'src'), '-I', str(temp / 'include'), str(cpp),
                    *map(str, extra), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


with tempfile.TemporaryDirectory(prefix='aqualight-ble-') as directory:
    temp = Path(directory)
    includes = temp / 'include'
    (includes / 'freertos').mkdir(parents=True)
    for name, value in {'NimBLEDevice.h': NIMBLE, 'Arduino.h': '#pragma once\n#include <cstdint>\nuint32_t millis();\n',
                        'freertos/FreeRTOS.h': FREERTOS, 'freertos/queue.h': QUEUE}.items():
        (includes / name).write_text(value)
    run('protocol', PROTOCOL, temp)
    run('adapter', ADAPTER, temp, [ROOT / 'src/ble_link.cpp'])

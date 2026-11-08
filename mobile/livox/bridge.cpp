// Receives through official Livox SDK2; forwards only the selected sensor to a private loopback socket.
#include "livox_lidar_api.h"
#include "livox_lidar_def.h"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <csignal>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <thread>
static volatile std::sig_atomic_t running=1;
static int output_fd=-1;
static sockaddr_in target{};
static uint32_t expected_ip=0;
void stop(int){running=0;}
void packet(uint32_t handle,uint8_t type,LivoxLidarEthernetPacket* p,void*){
 if(!p||handle!=expected_ip||p->length<36||p->length>1400)return;
 unsigned char out[1409];std::memcpy(out,"WSLV",4);std::memcpy(out+4,&handle,4);out[8]=type;
 std::memcpy(out+9,p,p->length);
 sendto(output_fd,out,p->length+9,MSG_DONTWAIT,reinterpret_cast<sockaddr*>(&target),sizeof(target));
}
void ack(livox_status status,uint32_t,LivoxLidarAsyncControlResponse* response,void*){
 if(status!=kLivoxLidarStatusSuccess||!response||response->ret_code!=0)
  std::fprintf(stderr,"Livox configuration failed: status=%d return=%d\n",int(status),response?int(response->ret_code):-1);
}
void info(uint32_t handle,const LivoxLidarInfo* p,void*){
 if(!p||handle!=expected_ip)return;
 // No IP/firmware changes, no reboot, no factory reset, and no robot motor access.
 std::fprintf(stderr,"Livox discovered: model=%u ip=%.15s\n",unsigned(p->dev_type),p->lidar_ip);
 SetLivoxLidarPclDataType(handle,kLivoxLidarCartesianCoordinateHighData,ack,nullptr);
 SetLivoxLidarWorkMode(handle,kLivoxLidarNormal,ack,nullptr);
}
int main(int argc,char**argv){
 if(argc!=4){std::fprintf(stderr,"Usage: livox-bridge CONFIG LIDAR_IP LOOPBACK_PORT\n");return 2;}
 int port=std::atoi(argv[3]);if(port<1024||port>65535||inet_pton(AF_INET,argv[2],&expected_ip)!=1)return 2;
 output_fd=socket(AF_INET,SOCK_DGRAM,0);if(output_fd<0)return 3;
 target.sin_family=AF_INET;target.sin_port=htons(port);inet_pton(AF_INET,"127.0.0.1",&target.sin_addr);
 std::signal(SIGINT,stop);std::signal(SIGTERM,stop);
 if(!LivoxLidarSdkInit(argv[1])){std::fprintf(stderr,"Livox SDK initialization failed; check host IP and port ownership\n");LivoxLidarSdkUninit();return 4;}
 SetLivoxLidarPointCloudCallBack(packet,nullptr);SetLivoxLidarImuDataCallback(packet,nullptr);SetLivoxLidarInfoChangeCallback(info,nullptr);
 while(running)std::this_thread::sleep_for(std::chrono::milliseconds(100));
 LivoxLidarSdkUninit();close(output_fd);return 0;
}

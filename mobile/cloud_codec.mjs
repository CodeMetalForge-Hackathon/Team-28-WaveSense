export function decodePreview(buffer){
 const d=new DataView(buffer);if(d.byteLength<32||d.getUint32(0,false)!==0x57533344||d.getUint8(4)!==1||d.getUint8(5)!==8||d.getUint16(6,true)!==32)throw Error('Invalid point-cloud header');
 const n=d.getUint32(12,true);if(n>18000||d.byteLength!==32+n*8)throw Error('Invalid point-cloud length');
 const positions=new Float32Array(n*3),intensity=new Uint8Array(n);for(let i=0;i<n;i++){const p=32+i*8;for(let a=0;a<3;a++)positions[i*3+a]=d.getInt16(p+a*2,true)*.01;intensity[i]=d.getUint8(p+6)}
 return{positions,intensity,count:n,sequence:d.getUint32(8,true),age:d.getFloat32(24,true),window:d.getFloat32(28,true)};
}

/* Reduced native-C reproductions from cpp100 cases 14, 23 and 46. */
#include <stdint.h>
__sfr __at(0x99) SBUF;
static inline uint32_t add(uint32_t a,uint32_t b){uint32_t r=a+b;return r;}
static inline uint32_t mul(uint32_t a,uint32_t b){uint32_t r=a*b;return r;}
static volatile uint32_t input;

#if TEST == 14
static uint32_t test(uint32_t x) {
  int32_t a=(int32_t)x;
  return add(add(add((uint32_t)(unsigned char)(a<0),
    mul(2,(uint32_t)(unsigned char)(a>-257L))),
    mul(4,(uint32_t)(unsigned char)(a==255))),
    mul(8,(uint32_t)(unsigned char)(a<=32767)));
}
#elif TEST == 23
static inline uint16_t add16(uint16_t a,uint16_t b){uint16_t r=a+b;return r;}
static inline uint16_t and16(uint16_t a,uint16_t b){uint16_t r=a&b;return r;}
static uint32_t test(uint32_t x) {
  uint32_t _2,_3,_7,_10,_11,_13;
  uint16_t _4,_5,_6,_8,_9,_12;
  _2=x;_3=0;_4=0;goto _14;
  do {
_14:_5=_4;if(_5<32)goto _15;else goto _16;
_15:_6=_4;_7=_2;if((uint32_t)_6==add(_7&15,16))goto _17;else goto _18;
_18:_8=_4;if(and16(_8,1)!=0)goto _19;else goto _20;
_20:_9=_4;_10=_2;_11=_3;_3=add(_11,mul((uint32_t)_9,_10));goto _21;
_19:goto _21;
_21:_12=_4;_4=add16(_12,1);goto _14;
  }while(1);
_17:goto _16;
_16:_13=_3;return _13;
}
#elif TEST == 46
struct P{uint32_t a;uint16_t b;uint8_t c;};
static struct P make(uint32_t x) {
  struct P result;struct P*p=&result;
  p->a=x^17;p->b=(uint16_t)(x>>8);p->c=(uint8_t)x;return result;
}
static uint32_t test(uint32_t x) {
  struct P p=make(x);uint32_t a=p.a;uint16_t b=p.b;uint8_t c=p.c;
  return add(add(a,mul((uint32_t)b,257)),(uint32_t)c);
}
#endif
static const uint32_t values[]={0,1,2,3,7,15,31,127,255,256,257,65535,65536,
  0x7fffffffUL,0x80000000UL,0xffffffffUL,0x12345678UL,0x89abcdefUL,0x01020304UL,0xffff0001UL};
static void hex(uint32_t x){uint8_t i;for(i=0;i<8;++i){uint8_t b=x>>28;SBUF=b<10?'0'+b:'A'+b-10;x<<=4;}SBUF='\n';}
void main(void){uint8_t i;for(i=0;i<20;++i){input=values[i];hex(test(input));}SBUF='D';SBUF='O';SBUF='N';SBUF='E';SBUF='\n';for(;;){}}

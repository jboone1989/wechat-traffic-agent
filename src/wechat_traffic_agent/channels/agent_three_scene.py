"""Real WebGL humanoid hero scene for the Content Agent homepage."""
from __future__ import annotations

MODEL_URL = (
    "https://cdn.jsdelivr.net/gh/Lucasmind/nodeava-workshop-public@"
    "d0c36947820adcb36e018cfc7b2318d47ff16a5b/frontend/public/avatars/rpm-female.glb"
)


def three_scene_css() -> str:
    return r'''
.crl-head{overflow:visible}.crl-head canvas{position:absolute;inset:-9% -13% -11%;width:126%!important;height:120%!important;display:block;opacity:0;transition:opacity .55s ease;pointer-events:none;filter:drop-shadow(0 30px 42px rgba(0,0,0,.42))}.crl-head.is-webgl-ready canvas{opacity:1}.crl-head .crl-head-fallback{position:absolute;inset:13% 24% 7%;border-radius:48% 48% 42% 42%;background:radial-gradient(circle at 50% 34%,rgba(228,236,231,.58),rgba(88,119,104,.22) 38%,rgba(7,17,15,.05) 68%,transparent 72%);transition:opacity .45s ease;filter:drop-shadow(0 25px 38px rgba(0,0,0,.32))}.crl-head .crl-head-fallback:after{content:"";position:absolute;left:50%;top:42%;width:82%;height:2px;background:linear-gradient(90deg,transparent,#64efad,transparent);transform:translateX(-50%);box-shadow:0 0 18px rgba(100,239,173,.6)}.crl-head.is-webgl-ready .crl-head-fallback{opacity:0}.crl-visual.webgl-active{cursor:grab}.crl-visual.webgl-active.is-dragging{cursor:grabbing}.crl-visual.webgl-active .drag-hint span:after{content:" · 360°";color:#70f2b0}.crl-head.is-webgl-ready:after{content:"";position:absolute;inset:12% 13% 8%;border-radius:50%;background:radial-gradient(circle at 68% 38%,rgba(102,245,178,.12),transparent 36%);pointer-events:none;mix-blend-mode:screen}
@media(max-width:620px){.crl-head canvas{inset:-7% -10% -9%;width:120%!important;height:116%!important}}
'''


def three_scene_script() -> str:
    script = r'''<script type="module">
import * as THREE from "https://esm.sh/three@0.180.0";
import { GLTFLoader } from "https://esm.sh/three@0.180.0/examples/jsm/loaders/GLTFLoader.js";
const MODEL_URL=__MODEL_URL__;
const sceneEl=document.getElementById("agent-scene"),mount=document.getElementById("agent-head"),canvas=document.getElementById("agent-canvas");
if(sceneEl&&mount&&canvas){
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
const renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:"high-performance"});
renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.7));renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.08;renderer.setClearColor(0x000000,0);
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(29,1,.01,50);camera.position.set(0,.015,1.03);camera.lookAt(0,-.015,0);
const pivot=new THREE.Group();scene.add(pivot);
scene.add(new THREE.HemisphereLight(0xf0f8f4,0x06100d,1.9));
const key=new THREE.DirectionalLight(0xffffff,4.6);key.position.set(-2.2,3.1,4.2);scene.add(key);
const rim=new THREE.PointLight(0x3af0ad,14,7.5);rim.position.set(2.1,.8,1.5);scene.add(rim);
const fill=new THREE.PointLight(0x8fc9ff,6.5,7);fill.position.set(-2.2,-.15,2.0);scene.add(fill);
const warm=new THREE.PointLight(0xffc3ad,2.2,5);warm.position.set(.25,-.45,1.8);scene.add(warm);
'''
    return script.replace("__MODEL_URL__", repr(MODEL_URL)) + _scene_script_tail()


def _scene_script_tail() -> str:
    return r'''
const metal=new THREE.MeshPhysicalMaterial({color:0xc9d3cf,metalness:.9,roughness:.18,clearcoat:.65,clearcoatRoughness:.17});
const dark=new THREE.MeshPhysicalMaterial({color:0x0b1311,metalness:.88,roughness:.2,clearcoat:.45});
const glow=new THREE.MeshStandardMaterial({color:0x50f0b5,emissive:0x22d999,emissiveIntensity:2.0,metalness:.35,roughness:.2});
const addCybernetics=(head)=>{const rig=new THREE.Group();head.add(rig);for(const side of [-1,1]){const temple=new THREE.Mesh(new THREE.BoxGeometry(.026,.084,.014),metal);temple.position.set(side*.118,.074,.104);temple.rotation.z=side*.18;rig.add(temple);const cheekRail=new THREE.Mesh(new THREE.BoxGeometry(.006,.058,.007),glow);cheekRail.position.set(side*.109,.031,.116);cheekRail.rotation.z=side*.30;rig.add(cheekRail);const earRing=new THREE.Mesh(new THREE.TorusGeometry(.026,.005,10,36),glow);earRing.position.set(side*.142,.040,.004);earRing.rotation.y=Math.PI/2;rig.add(earRing);const earHub=new THREE.Mesh(new THREE.CylinderGeometry(.016,.016,.014,24),dark);earHub.position.copy(earRing.position);earHub.rotation.z=Math.PI/2;rig.add(earHub)}return rig};
const addNeckHardware=()=>{const g=new THREE.Group();g.position.y=-.153;pivot.add(g);const ring=new THREE.Mesh(new THREE.TorusGeometry(.108,.004,8,56),glow);ring.rotation.x=Math.PI/2;g.add(ring);return g};
let targetYaw=0,targetPitch=0,currentYaw=0,currentPitch=0,drag=false,lastX=0,lastY=0,intro=true,introStart=-1,head=null,headBaseY=0;
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const resize=()=>{const w=Math.max(1,mount.clientWidth),h=Math.max(1,mount.clientHeight);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix()};new ResizeObserver(resize).observe(mount);resize();
new GLTFLoader().load(MODEL_URL,gltf=>{const model=gltf.scene;model.traverse(o=>{if(!o.isMesh)return;if(/Glasses/.test(o.name))o.visible=false;if(o.material){o.material=o.material.clone();if(/Skin|Body/.test(o.material.name)){o.material.roughness=.48;o.material.metalness=.015}if(/Outfit/.test(o.material.name)){o.material.map=null;o.material.color.set(0x121918);o.material.metalness=.62;o.material.roughness=.28}}});
head=model.getObjectByName("Head");const box=new THREE.Box3().setFromObject(model),size=box.getSize(new THREE.Vector3());const headCenter=box.max.y-size.y*.12;model.position.y=-headCenter;pivot.add(model);if(head){headBaseY=head.rotation.y;addCybernetics(head);head.rotation.y=headBaseY-(reduce?0:.82);mount.dataset.headYaw=head.rotation.y.toFixed(3)}addNeckHardware();introStart=-1;mount.classList.add("is-webgl-ready");sceneEl.classList.add("webgl-active")},undefined,err=>console.warn("Content Agent 3D model unavailable; using static fallback.",err));
sceneEl.addEventListener("pointerdown",e=>{if(e.button!==0)return;drag=true;intro=false;lastX=e.clientX;lastY=e.clientY;sceneEl.classList.add("is-dragging");sceneEl.setPointerCapture(e.pointerId)});
sceneEl.addEventListener("pointermove",e=>{if(!drag)return;const dx=e.clientX-lastX,dy=e.clientY-lastY;lastX=e.clientX;lastY=e.clientY;targetYaw+=dx*.0105;targetPitch=clamp(targetPitch+dy*.0065,-.36,.36)});
const release=e=>{drag=false;sceneEl.classList.remove("is-dragging");try{sceneEl.releasePointerCapture(e.pointerId)}catch{}};sceneEl.addEventListener("pointerup",release);sceneEl.addEventListener("pointercancel",release);
const ease=t=>1-Math.pow(1-t,4);
const animate=now=>{requestAnimationFrame(animate);if(head&&intro&&!reduce){if(introStart<0)introStart=now+500;const t=clamp((now-introStart)/3400,0,1);head.rotation.y=headBaseY-.82*(1-ease(t));mount.dataset.headYaw=head.rotation.y.toFixed(3);if(t>=1)intro=false}currentYaw+=(targetYaw-currentYaw)*.075;currentPitch+=(targetPitch-currentPitch)*.075;pivot.rotation.y=currentYaw;pivot.rotation.x=currentPitch;const idle=drag||reduce?0:Math.sin(now*.00055)*.009;pivot.rotation.z=idle;glow.emissiveIntensity=1.8+Math.sin(now*.0021)*.28;renderer.render(scene,camera)};requestAnimationFrame(animate);
}
</script>'''

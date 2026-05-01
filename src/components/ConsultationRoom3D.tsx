import { Canvas } from '@react-three/fiber';
import { ContactShadows, Html, OrbitControls, Text } from '@react-three/drei';
import { useMemo } from 'react';
import type { AiEvent, Consultation, Message } from '../types';

interface Props {
  consultation?: Consultation;
  latestMessage?: Message;
  aiEvent?: AiEvent;
}

const statusColor: Record<string, string> = {
  waiting_human: '#f7c948',
  assigned: '#5ab7f2',
  active: '#48bb78',
  needs_expert_review: '#f56565',
  resolved: '#9f7aea',
};

function CounterScene({ consultation, latestMessage, aiEvent }: Props) {
  const initials = useMemo(() => {
    if (!consultation) return 'MY';
    return consultation.customer_name
      .split(' ')
      .map((part) => part[0])
      .slice(0, 2)
      .join('')
      .toUpperCase();
  }, [consultation]);

  const speech = latestMessage?.content ?? aiEvent?.summary ?? 'Waiting for the next consultation.';
  const ticket = consultation?.queue_number ?? 'SUP-0000';
  const status = consultation?.status ?? 'waiting_human';

  return (
    <>
      <color attach="background" args={['#f6efe2']} />
      <ambientLight intensity={0.85} />
      <directionalLight position={[1.5, 5, 3]} intensity={1.25} castShadow />
      <group position={[0, -0.1, 0]}>
        <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]}>
          <planeGeometry args={[9, 7]} />
          <meshStandardMaterial color="#c99d68" />
        </mesh>
        <mesh receiveShadow position={[0, 1.7, -2.7]}>
          <boxGeometry args={[9, 3.5, 0.12]} />
          <meshStandardMaterial color="#d4c8b1" />
        </mesh>
        <mesh position={[0, 0.85, -2.63]}>
          <boxGeometry args={[9, 1.4, 0.14]} />
          <meshStandardMaterial color="#bd8f60" />
        </mesh>
        <mesh position={[0, 3.05, -0.35]}>
          <boxGeometry args={[3.2, 0.12, 0.9]} />
          <meshStandardMaterial color="#fff8d7" emissive="#fff2ad" emissiveIntensity={0.5} />
        </mesh>
        <mesh position={[0, 3.0, -0.35]}>
          <boxGeometry args={[3.45, 0.18, 1.05]} />
          <meshStandardMaterial color="#332016" />
        </mesh>
        <mesh position={[0, 1.15, -1.75]}>
          <boxGeometry args={[2.9, 1.4, 0.35]} />
          <meshStandardMaterial color="#765035" />
        </mesh>
        <mesh position={[0, 1.9, -1.72]}>
          <boxGeometry args={[3.2, 0.16, 0.45]} />
          <meshStandardMaterial color="#f25f3a" />
        </mesh>
        <mesh position={[0, 1.15, -1.5]}>
          <boxGeometry args={[2.25, 1.1, 0.18]} />
          <meshStandardMaterial color="#fffaf0" />
        </mesh>
        <Text position={[0, 1.53, -1.38]} fontSize={0.16} color="#2f241f" anchorX="center">
          SERVICE COUNTER
        </Text>
        <Text position={[0, 1.24, -1.37]} fontSize={0.3} color="#2f241f" anchorX="center">
          {ticket}
        </Text>
        <mesh position={[0, 1.62, -1.27]}>
          <boxGeometry args={[1.35, 0.16, 0.08]} />
          <meshStandardMaterial color={statusColor[status]} />
        </mesh>
        <Text position={[0, 1.62, -1.21]} fontSize={0.08} color="#2f241f" anchorX="center">
          {status.replace(/_/g, ' ').toUpperCase()}
        </Text>

        <group position={[0, 0.34, -0.45]}>
          <mesh castShadow position={[0, 0.28, 0]}>
            <capsuleGeometry args={[0.28, 0.7, 10, 18]} />
            <meshStandardMaterial color="#f0a43b" />
          </mesh>
          <mesh castShadow position={[0, 0.92, 0]}>
            <sphereGeometry args={[0.28, 32, 16]} />
            <meshStandardMaterial color="#8a553e" />
          </mesh>
          <mesh castShadow position={[0, 1.14, -0.02]}>
            <boxGeometry args={[0.6, 0.18, 0.38]} />
            <meshStandardMaterial color="#2f2018" />
          </mesh>
          <Text position={[0, 0.96, 0.3]} fontSize={0.16} color="#fffaf0" anchorX="center">
            {initials}
          </Text>
          <mesh position={[-0.44, 0.25, 0]}>
            <capsuleGeometry args={[0.1, 0.55, 8, 16]} />
            <meshStandardMaterial color="#e99a34" />
          </mesh>
          <mesh position={[0.44, 0.25, 0]}>
            <capsuleGeometry args={[0.1, 0.55, 8, 16]} />
            <meshStandardMaterial color="#e99a34" />
          </mesh>
        </group>

        <group position={[-2.7, 0.45, -1.15]}>
          <mesh>
            <boxGeometry args={[1.2, 1.35, 0.1]} />
            <meshStandardMaterial color="#8a5a34" />
          </mesh>
          {[-0.32, 0, 0.32].map((x, index) => (
            <mesh key={x} position={[x, 0.22 - index * 0.28, 0.08]}>
              <boxGeometry args={[0.36, 0.2, 0.04]} />
              <meshStandardMaterial color={index === 1 ? '#f7d070' : '#fffaf0'} />
            </mesh>
          ))}
        </group>
        <group position={[2.75, 0.7, -1.1]}>
          <mesh>
            <boxGeometry args={[1.28, 1.55, 0.12]} />
            <meshStandardMaterial color="#fffaf0" />
          </mesh>
          <Text position={[0, 0.54, 0.08]} fontSize={0.11} color="#2f241f" anchorX="center">
            SUPPORT CASE
          </Text>
          <Text position={[0, 0.22, 0.08]} fontSize={0.1} color="#2f241f" anchorX="center">
            {consultation?.topic.slice(0, 18) ?? 'Ready'}
          </Text>
          <Text position={[0, -0.08, 0.08]} fontSize={0.08} color="#6b5a4f" anchorX="center">
            {aiEvent?.classification ?? 'No case selected'}
          </Text>
        </group>

        <mesh position={[-3.6, 0.5, 0.65]}>
          <boxGeometry args={[1.5, 0.14, 0.62]} />
          <meshStandardMaterial color="#2b6f73" />
        </mesh>
        <mesh position={[2.0, 0.2, 0.75]}>
          <boxGeometry args={[1.0, 0.14, 0.65]} />
          <meshStandardMaterial color="#7a4d2b" />
        </mesh>
        <mesh position={[1.7, 0.58, 0.72]}>
          <boxGeometry args={[0.42, 0.22, 0.18]} />
          <meshStandardMaterial color="#fffaf0" />
        </mesh>
        <mesh position={[2.1, 0.6, 0.72]}>
          <boxGeometry args={[0.16, 0.3, 0.16]} />
          <meshStandardMaterial color="#ef6f59" />
        </mesh>

        <Html position={[0, 2.25, -0.7]} center distanceFactor={6}>
          <div className="speech">
            <strong>{consultation?.customer_name ?? 'No active case'}</strong>
            <span>{speech.length > 120 ? `${speech.slice(0, 120)}...` : speech}</span>
          </div>
        </Html>
      </group>
      <ContactShadows position={[0, -0.05, 0]} opacity={0.35} scale={8} blur={2} far={2} />
    </>
  );
}

export function ConsultationRoom3D(props: Props) {
  return (
    <div className="room-frame" aria-label="3D active consultation room">
      <Canvas shadows camera={{ position: [0, 2.3, 4.4], fov: 45 }}>
        <CounterScene {...props} />
        <OrbitControls enablePan={false} enableZoom={false} minPolarAngle={1.1} maxPolarAngle={1.45} />
      </Canvas>
    </div>
  );
}

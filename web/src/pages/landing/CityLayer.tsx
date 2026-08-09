/**
 * The props and the traffic, drawn.
 *
 * Fifteen kinds of object collapse into three instanced meshes — box, cylinder, sphere —
 * because a mosque dome and a satellite dish are the same primitive at different sizes,
 * and the GPU does not care which is which. Placement and meaning live in `cityProps.ts`;
 * this file only puts matrices into buffers.
 *
 * Traffic is the one thing updated per frame. Everything else is written once at mount:
 * a wall does not move, and rewriting forty thousand static matrices every frame to
 * animate two hundred cars would be the most expensive mistake available here.
 */

import { useFrame } from "@react-three/fiber";
import { useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";

import {
  type CityProps as Props,
  type Vehicle,
  buildTraffic,
  samplePath,
} from "./cityProps";
import type { LahoreExtract } from "./lahore";

/** Write a list of placed primitives into an instanced mesh, once. */
function useStatic(props: Props["boxes"]) {
  const mesh = useRef<THREE.InstancedMesh>(null);

  useLayoutEffect(() => {
    const node = mesh.current;
    if (!node) return;

    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const scale = new THREE.Vector3();
    const euler = new THREE.Euler();
    const colour = new THREE.Color();

    for (let i = 0; i < props.length; i++) {
      const prop = props[i]!;
      position.set(prop.x, prop.y, prop.z);
      euler.set(0, -prop.r, 0);
      quaternion.setFromEuler(euler);
      scale.set(prop.w, prop.h, prop.d);
      matrix.compose(position, quaternion, scale);
      node.setMatrixAt(i, matrix);
      colour.setRGB(prop.c[0], prop.c[1], prop.c[2], THREE.SRGBColorSpace);
      node.setColorAt(i, colour);
    }

    node.instanceMatrix.needsUpdate = true;
    if (node.instanceColor) node.instanceColor.needsUpdate = true;
  }, [props]);

  return mesh;
}

function Traffic({ vehicles }: { vehicles: Vehicle[] }) {
  const mesh = useRef<THREE.InstancedMesh>(null);

  useLayoutEffect(() => {
    const node = mesh.current;
    if (!node) return;
    const colour = new THREE.Color();
    for (let i = 0; i < vehicles.length; i++) {
      const vehicle = vehicles[i]!;
      colour.setRGB(vehicle.c[0], vehicle.c[1], vehicle.c[2], THREE.SRGBColorSpace);
      node.setColorAt(i, colour);
    }
    if (node.instanceColor) node.instanceColor.needsUpdate = true;
  }, [vehicles]);

  useFrame((_, delta) => {
    const node = mesh.current;
    if (!node) return;
    // Clamped: a backgrounded tab hands back a delta of several seconds, and without this
    // every vehicle teleports somewhere down its route on the frame the tab regains focus.
    const step = Math.min(delta, 0.05);

    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const quaternion = new THREE.Quaternion();
    const scale = new THREE.Vector3();
    const euler = new THREE.Euler();

    for (let i = 0; i < vehicles.length; i++) {
      const vehicle = vehicles[i]!;
      vehicle.at += vehicle.speed * step;

      const point = samplePath(vehicle.path, vehicle.at);
      // samplePath reports the path's own length once it runs off the end; wrap there so
      // traffic loops rather than piling up on the last vertex.
      if (point.total > 0) {
        vehicle.at = 0;
        continue;
      }

      position.set(
        point.x - Math.sin(point.angle) * vehicle.lane,
        vehicle.height * 0.5,
        point.z + Math.cos(point.angle) * vehicle.lane,
      );
      euler.set(0, -point.angle, 0);
      quaternion.setFromEuler(euler);
      scale.set(vehicle.length, vehicle.height, vehicle.width);
      matrix.compose(position, quaternion, scale);
      node.setMatrixAt(i, matrix);
    }

    node.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh
      ref={mesh}
      args={[null!, null!, vehicles.length]}
      frustumCulled={false}
      castShadow
    >
      <boxGeometry args={[1, 1, 1]} />
      <meshLambertMaterial />
    </instancedMesh>
  );
}

export default function CityLayer({
  props,
  data,
  animate,
}: {
  props: Props;
  data: LahoreExtract;
  animate: boolean;
}) {
  const boxes = useStatic(props.boxes);
  const cylinders = useStatic(props.cylinders);
  const spheres = useStatic(props.spheres);
  const vehicles = useMemo(() => buildTraffic(data), [data]);

  return (
    <>
      {/* Walls, awnings, pitches and forecourts are the things a shadow lands *on* as
          often as they throw one, so the boxes do both. The cylinders and spheres — lamp
          posts, minarets, domes, dishes — only cast: they are thin or convex, and a
          second pass over them buys nothing the eye can find. */}
      <instancedMesh
        ref={boxes}
        args={[null!, null!, props.boxes.length]}
        frustumCulled={false}
        castShadow
        receiveShadow
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshLambertMaterial />
      </instancedMesh>

      <instancedMesh
        ref={cylinders}
        args={[null!, null!, props.cylinders.length]}
        frustumCulled={false}
        castShadow
      >
        {/* Six sides. At the distance a lamp post is seen from, a rounder cylinder is
            vertices spent on nothing. */}
        <cylinderGeometry args={[0.5, 0.5, 1, 6]} />
        <meshLambertMaterial />
      </instancedMesh>

      <instancedMesh
        ref={spheres}
        args={[null!, null!, props.spheres.length]}
        frustumCulled={false}
        castShadow
      >
        <sphereGeometry args={[0.5, 10, 6]} />
        <meshLambertMaterial />
      </instancedMesh>

      {animate && <Traffic vehicles={vehicles} />}
    </>
  );
}

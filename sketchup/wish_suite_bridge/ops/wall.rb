require 'json'
require_relative '../logger'

module WishSuiteBridge
  module Ops
    # SketchUp attributes cannot hold Ruby Hashes (they read back as nil), so
    # structured specs are stored as JSON strings. Legacy Hash/Array values are
    # still accepted when reading.
    module SpecStore
      DICT = "su_mcp_bridge".freeze

      def self.write(entity, key, value)
        entity.set_attribute(DICT, key, JSON.generate(value))
      end

      def self.read(entity, key, default = nil)
        raw = entity.get_attribute(DICT, key)
        return default if raw.nil?
        return raw unless raw.is_a?(String)
        JSON.parse(raw)
      rescue JSON::ParserError
        default
      end

      # A boolean subtract erases the original wall and its result group does
      # not reliably keep attributes, tag or material. Take a snapshot before
      # the subtract and restore it onto the result afterwards.
      def self.snapshot(entity)
        dict = entity.attribute_dictionary(DICT)
        attrs = {}
        dict.each_pair { |k, v| attrs[k] = v } if dict
        { attrs: attrs, layer: entity.layer, material: entity.material }
      end

      def self.restore(entity, snap)
        return unless entity && entity.valid? && snap
        snap[:attrs].each { |k, v| entity.set_attribute(DICT, k, v) }
        entity.layer = snap[:layer] if snap[:layer] && snap[:layer].valid?
        entity.material = snap[:material] if snap[:material] && snap[:material].valid? && entity.material.nil?
      end
    end

    module Wall
      # Conversion factor (1 inch = 25.4 mm). Centralized so we don't sprinkle
      # the literal across the file.
      MM_TO_IN = 1.0 / 25.4

      # ----------------------------------------------------------------------
      # CREATE
      # ----------------------------------------------------------------------
      def self.create(params)
        model = Sketchup.active_model
        ents  = model.active_entities

        coords = _read_centerline(params)
        return { "error" => "zero-length wall" } if coords[:len] < 0.001

        ai_id = params["ai_id"] || params["id"]
        upserted = false
        unless ai_id && !ai_id.empty?
          cl_mm = params["centerline"] ? params["centerline"].first : [params["start_x"].to_f * 25.4, params["start_y"].to_f * 25.4]
          ai_id = WishSuiteBridge::AI_ID.auto_name(:wall, cl_mm)
        else
          existing = WishSuiteBridge::AI_ID.find(ai_id)
          if existing && existing.valid?
            existing.erase!
            WishSuiteBridge::AI_ID.evict(ai_id)
            upserted = true
          end
        end
        level = params["level"] || "GF"
        tags  = params["tags"] || []

        group = _build_wall_group(ents, coords)
        # Optional base elevation (multi-storey). Geometry stays at z=0 inside
        # the group; the group is moved, and opening cuts read the group
        # transformation, so offsets and sills stay relative to the wall base.
        base_z = (params["base_z_mm"] || 0).to_f * MM_TO_IN
        group.transform!(Geom::Transformation.translation([0, 0, base_z])) if base_z.abs > 1e-6
        # World-space centerline incl. base elevation. A boolean subtract may
        # return a group whose transformation differs from the original, so
        # opening cuts prefer this over local coords + transformation.
        SpecStore.write(group, "centerline_world_in",
                        [[coords[:x1], coords[:y1], base_z], [coords[:x2], coords[:y2], base_z]])
        _assign_layer(group, model, level)

        WishSuiteBridge::AI_ID.set(group, ai_id, tags: tags)

        # Persist the creation params on the group so `opening.modify` (or any
        # future "rebuild this wall" op) can reconstruct the wall verbatim.
        # The attribute survives boolean subtract because SketchUp copies the
        # attribute dictionary onto the surviving group.
        SpecStore.write(group, "wall_spec", params)
        # Centerline start/direction in inches, used to measure opening offsets
        # from the wall's real start point instead of its lowest-coordinate end.
        SpecStore.write(group, "centerline_in", [[coords[:x1], coords[:y1]], [coords[:x2], coords[:y2]]])
        SpecStore.write(group, "thickness_in", coords[:thick])
        group.set_attribute("su_mcp_bridge", "type", "wall")

        WishSuiteBridge::Logger.debug("ops.wall.create #{ai_id} -> guid=#{group.guid} (#{upserted ? 'upserted' : 'created'})")

        {
          "ai_id"  => ai_id,
          "guid"   => group.guid,
          "status" => upserted ? "upserted" : "created",
          "bounds" => {
            "min" => group.bounds.min.to_a.map { |v| (v * 25.4).round(3) },
            "max" => group.bounds.max.to_a.map { |v| (v * 25.4).round(3) }
          }
        }
      end

      # ----------------------------------------------------------------------
      # MODIFY (delete + recreate, preserving ai_id and any opening cuts on it)
      # ----------------------------------------------------------------------
      #
      # SketchUp does not let you reliably edit a wall solid in place after
      # boolean ops. Strategy: erase the wall group AND every opening that
      # cuts it from the AI's perspective, then rebuild the wall from the new
      # parameters. The CALLER (apply.py / sync engine) is responsible for
      # re-cutting the openings on the new wall.
      def self.modify(params)
        ai_id = params["ai_id"] || params["id"]
        return { "error" => "ai_id is required for ops.wall.modify" } unless ai_id

        model = Sketchup.active_model
        existing = WishSuiteBridge::AI_ID.find(ai_id)

        if existing && existing.valid?
          model.start_operation("Modify wall #{ai_id}", true)
          existing.erase!
          model.commit_operation
          WishSuiteBridge::AI_ID.evict(ai_id)
        else
          WishSuiteBridge::Logger.warn("ops.wall.modify: existing wall #{ai_id} not found; creating fresh")
        end

        create(params)
      end

      # ----------------------------------------------------------------------
      # DELETE
      # ----------------------------------------------------------------------
      def self.delete(params)
        ai_id = params["ai_id"] || params["id"]
        ent = WishSuiteBridge::AI_ID.find(ai_id)
        return { "error" => "Wall not found: #{ai_id}" } unless ent

        model = Sketchup.active_model
        model.start_operation("Delete wall #{ai_id}", true)
        ent.erase!
        model.commit_operation
        WishSuiteBridge::AI_ID.evict(ai_id)

        { "ai_id" => ai_id, "status" => "deleted" }
      end

      # ======================================================================
      # Helpers
      # ======================================================================

      # Read centerline + thickness + height from either v1 (inches, start_x/end_x)
      # or v2 (mm, centerline:[[x,y],[x,y]]) parameter shape. Returns inches.
      def self._read_centerline(params)
        if params["centerline"]
          cl = params["centerline"]
          x1 = cl[0][0].to_f * MM_TO_IN
          y1 = cl[0][1].to_f * MM_TO_IN
          x2 = cl[1][0].to_f * MM_TO_IN
          y2 = cl[1][1].to_f * MM_TO_IN
          thick = params["thickness_mm"].to_f * MM_TO_IN
          height = params["height_mm"].to_f * MM_TO_IN
        else
          x1 = params["start_x"].to_f
          y1 = params["start_y"].to_f
          x2 = params["end_x"].to_f
          y2 = params["end_y"].to_f
          thick = params["thickness"].to_f
          height = params["height"].to_f
        end

        dx = x2 - x1
        dy = y2 - y1
        len = Math.sqrt(dx*dx + dy*dy)

        {
          x1: x1, y1: y1, x2: x2, y2: y2,
          dx: dx, dy: dy,
          len: len,
          thick: thick, height: height
        }
      end

      # Build the 4 footprint corners + extrude.
      def self._build_wall_group(ents, c)
        # Perpendicular offset (proven v1 math): rotate (dx,dy)/len by +90deg.
        len = c[:len]
        px = (-c[:dy] / len) * (c[:thick] / 2.0)
        py = ( c[:dx] / len) * (c[:thick] / 2.0)

        p1 = Geom::Point3d.new(c[:x1] + px, c[:y1] + py, 0)
        p2 = Geom::Point3d.new(c[:x2] + px, c[:y2] + py, 0)
        p3 = Geom::Point3d.new(c[:x2] - px, c[:y2] - py, 0)
        p4 = Geom::Point3d.new(c[:x1] - px, c[:y1] - py, 0)

        group = ents.add_group
        face = group.entities.add_face(p1, p2, p3, p4)
        face.reverse! if face.normal.z < 0
        face.pushpull(c[:height])
        group
      end

      def self._assign_layer(group, model, level_name)
        tag = nil
        begin
          tag = model.layers.add(level_name)
        rescue
          tag = model.layers[level_name]
        end
        group.layer = tag if tag
      end
    end
  end
end

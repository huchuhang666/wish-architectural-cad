require_relative '../logger'
require_relative 'wall'

module WishSuiteBridge
  module Ops
    module Opening
      MM_TO_IN = 1.0 / 25.4
      # Doors (sill at floor level) get a cutter that starts this far below the
      # floor, so the cutter is not coplanar with the wall bottom.
      BELOW_FLOOR_IN = 1.0
      FRAME_SUFFIX = "__frame".freeze

      # 2026-10-22 fixes (re-applied; the 10-21 fixes were lost in the restore):
      #   * offset_mm is measured from the wall centerline START point (the
      #     first point passed to create_wall), not the lowest-coordinate end.
      #   * Door cutters (sill 0) extend below the floor and are always pushed
      #     upward, so the door hole actually appears.
      #   * Windows are cut at exactly sill..sill+height (batch_cut used to
      #     add 1 inch above and below, about 51 mm too tall).
      #   * wall_spec / openings_spec are stored as JSON (Hashes read back as
      #     nil) and are carried over to the wall left after each subtract,
      #     so a second opening keeps the first and modify works.
      #   * modify checks for wall_spec BEFORE erasing the wall, and removes
      #     the old door/window frames before re-cutting.

      # =========================================================================
      # CUT — boolean-subtract one opening from a wall
      # =========================================================================
      def self.cut(params)
        model = Sketchup.active_model

        ai_id = params["ai_id"] || params["id"]
        ai_id = WishSuiteBridge::AI_ID.auto_name(:opening) unless ai_id && !ai_id.to_s.empty?
        wall_ai_id = params["wall_id"]
        return { "error" => "wall_id is required" } unless wall_ai_id

        cut_params = _read_cut_params(params)
        bad = _validate_cut(cut_params)
        return { "error" => bad } if bad

        wall_group = WishSuiteBridge::AI_ID.find(wall_ai_id)
        return { "error" => "Wall not found: #{wall_ai_id}" } unless wall_group

        frame = _wall_local_frame(wall_group)
        return { "error" => "Could not determine wall frame for #{wall_ai_id}" } unless frame
        warn = _range_warning(frame, [cut_params])

        snap = SpecStore.snapshot(wall_group)
        result = _perform_cut(model, wall_group, frame, cut_params, params["generate_frame"])
        return result if result["error"]

        new_wall = result[:new_wall]
        SpecStore.restore(new_wall, snap)
        _record_opening_on_wall(new_wall, ai_id, wall_ai_id, params)
        _tag_frame(result[:frame_group], ai_id)

        tags = params["tags"] || []
        WishSuiteBridge::AI_ID.set(new_wall, wall_ai_id, tags: tags)
        WishSuiteBridge::AI_ID.replace_pointer(wall_ai_id, new_wall)

        out = {
          "ai_id"       => ai_id,
          "wall_id"     => wall_ai_id,
          "result_guid" => new_wall.guid,
          "status"      => "cut"
        }
        out["warning"] = warn if warn
        out
      end

      # =========================================================================
      # BATCH_CUT — cut ALL openings for a wall in ONE boolean operation
      # =========================================================================
      # Params:
      #   wall_id   (str)   : ai_id of the target wall
      #   openings  (array) : list of opening specs, each with:
      #     ai_id, offset_mm, width_mm, height_mm, sill_mm, generate_frame
      def self.batch_cut(params)
        model = Sketchup.active_model

        wall_ai_id = params["wall_id"]
        return { "error" => "wall_id is required" } unless wall_ai_id

        openings = params["openings"]
        return { "error" => "openings array is required" } unless openings.is_a?(Array) && !openings.empty?

        wall_group = WishSuiteBridge::AI_ID.find(wall_ai_id)
        return { "error" => "Wall not found: #{wall_ai_id}" } unless wall_group

        frame = _wall_local_frame(wall_group)
        return { "error" => "Could not determine wall frame for #{wall_ai_id}" } unless frame

        parsed = openings.map { |o| _read_cut_params(o) }
        parsed.each_with_index do |c, i|
          bad = _validate_cut(c)
          return { "error" => "opening #{i}: #{bad}" } if bad
        end
        warn = _range_warning(frame, parsed)

        full_transform = _local_to_world(frame)
        snap = SpecStore.snapshot(wall_group)

        model.start_operation("Batch cut #{openings.length} openings on #{wall_ai_id}", true)
        begin
          cutter = model.active_entities.add_group
          parsed.each do |c|
            sub = cutter.entities.add_group
            _add_cutter_box(sub.entities, frame, c)
            sub.explode
          end
          cutter.transform!(full_transform)

          new_wall = cutter.subtract(wall_group)
          if new_wall.nil?
            model.abort_operation
            return { "error" => "Boolean subtract returned nil for #{wall_ai_id}" }
          end
          SpecStore.restore(new_wall, snap)

          results = []
          openings.each_with_index do |o, i|
            ai_id = o["ai_id"] || WishSuiteBridge::AI_ID.auto_name(:opening)
            gen = o["generate_frame"]
            if gen && !gen.to_s.empty?
              c = parsed[i]
              fg = _build_frame_in_opening(model, full_transform, c[:offset], c[:offset] + c[:width],
                                           c[:sill], c[:sill] + c[:height], frame[:thickness], gen)
              _tag_frame(fg, ai_id)
            end
            _record_opening_on_wall(new_wall, ai_id, wall_ai_id, o)
            results << { "ai_id" => ai_id, "status" => "cut" }
          end

          tags = params["tags"] || []
          WishSuiteBridge::AI_ID.set(new_wall, wall_ai_id, tags: tags)
          WishSuiteBridge::AI_ID.replace_pointer(wall_ai_id, new_wall)
          model.commit_operation

          out = { "wall_id" => wall_ai_id, "openings" => results, "status" => "batch_cut", "count" => results.length }
          out["warning"] = warn if warn
          out
        rescue => e
          model.abort_operation
          WishSuiteBridge::Logger.error("batch_cut failed: #{e.message}")
          { "error" => "batch_cut failed: #{e.message}", "backtrace" => e.backtrace.first(5) }
        end
      end

      # =========================================================================
      # MODIFY — change an opening's offset/width/height/sill (or delete it)
      # =========================================================================
      # The boolean can't be reversed, so the wall is rebuilt from its saved
      # wall_spec and every opening on it is re-cut.
      def self.modify(params)
        ai_id = params["ai_id"] || params["id"]
        return { "error" => "ai_id is required" } unless ai_id

        model = Sketchup.active_model

        wall_ai_id = params["wall_id"] || _find_wall_for_opening(model, ai_id)
        return { "error" => "Could not locate wall for opening #{ai_id}" } unless wall_ai_id

        wall_group = WishSuiteBridge::AI_ID.find(wall_ai_id)
        return { "error" => "Wall not found: #{wall_ai_id}" } unless wall_group

        # Check everything needed for the rebuild BEFORE erasing anything.
        wall_spec = SpecStore.read(wall_group, "wall_spec")
        unless wall_spec.is_a?(Hash)
          return { "error" => "Wall #{wall_ai_id} has no readable wall_spec (made by an older plugin version?). " \
                              "Nothing was changed. Recreate the wall with create_wall, then cut the openings again." }
        end
        openings_spec = SpecStore.read(wall_group, "openings_spec", [])
        unless openings_spec.any? { |o| o["ai_id"] == ai_id } || params["delete"] != true
          return { "error" => "Opening #{ai_id} is not recorded on wall #{wall_ai_id}; nothing was changed." }
        end

        new_openings = openings_spec.reject { |o| o["ai_id"] == ai_id }
        if params["delete"] != true
          updated = openings_spec.find { |o| o["ai_id"] == ai_id } || { "ai_id" => ai_id, "wall_id" => wall_ai_id }
          updated = updated.dup
          %w[offset_mm width_mm height_mm sill_mm offset width height sill kind generate_frame].each do |k|
            updated[k] = params[k] if params.key?(k)
          end
          bad = _validate_cut(_read_cut_params(updated))
          return { "error" => "#{bad}; nothing was changed." } if bad
          new_openings << updated
        end

        material = wall_group.material
        model.start_operation("Modify opening #{ai_id}", true)
        openings_spec.each { |o| _erase_frame(model, o["ai_id"]) }
        wall_group.erase!
        WishSuiteBridge::AI_ID.evict(wall_ai_id)
        model.commit_operation

        created = WishSuiteBridge::Ops::Wall.create(wall_spec)
        return { "error" => "Wall rebuild failed: #{created['error']}" } if created["error"]
        rebuilt = WishSuiteBridge::AI_ID.find(wall_ai_id)
        rebuilt.material = material if rebuilt && material && material.valid?

        errors = []
        new_openings.each do |op|
          r = cut(op)
          errors << { "ai_id" => op["ai_id"], "error" => r["error"] } if r["error"]
        end

        out = { "ai_id" => ai_id, "wall_id" => wall_ai_id,
                "status" => params["delete"] == true ? "deleted" : "modified",
                "openings_on_wall" => new_openings.map { |o| o["ai_id"] } }
        out["recut_errors"] = errors unless errors.empty?
        out
      end

      def self.delete(params)
        modify(params.merge("delete" => true))
      end

      # ===========================================================================
      # Helpers
      # ===========================================================================

      def self._read_cut_params(params)
        if params["offset_mm"] || params["width_mm"]
          {
            offset: params["offset_mm"].to_f * MM_TO_IN,
            width:  params["width_mm"].to_f * MM_TO_IN,
            height: params["height_mm"].to_f * MM_TO_IN,
            sill:   (params["sill_mm"] || 0).to_f * MM_TO_IN
          }
        else
          {
            offset: params["offset"].to_f,
            width:  params["width"].to_f,
            height: params["height"].to_f,
            sill:   (params["sill"] || 0).to_f
          }
        end
      end

      def self._validate_cut(c)
        return "width must be > 0" unless c[:width] > 0
        return "height must be > 0" unless c[:height] > 0
        return "offset must be >= 0" if c[:offset] < 0
        nil
      end

      # Non-fatal warning when an opening runs past the wall end or top.
      def self._range_warning(frame, cuts)
        msgs = []
        cuts.each_with_index do |c, i|
          if c[:offset] + c[:width] > frame[:length] + 0.01
            msgs << "opening #{i} ends #{((c[:offset] + c[:width] - frame[:length]) * 25.4).round(1)} mm past the wall end"
          end
          if frame[:height] && c[:sill] + c[:height] > frame[:height] + 0.01
            msgs << "opening #{i} is #{((c[:sill] + c[:height] - frame[:height]) * 25.4).round(1)} mm taller than the wall"
          end
        end
        msgs.empty? ? nil : msgs.join("; ")
      end

      def self._local_to_world(frame)
        z_axis = Geom::Vector3d.new(0, 0, 1)
        local_to_world = Geom::Transformation.axes(frame[:origin], frame[:x_axis], frame[:y_axis], z_axis)
        center_shift = Geom::Transformation.translation(Geom::Vector3d.new(0, frame[:thickness] / 2.0, 0))
        local_to_world * center_shift
      end

      # Cutter box in local coords: x along wall, y across (centered), z up.
      def self._add_cutter_box(ents, frame, c)
        oversize = frame[:thickness] + 10.0
        x0 = c[:offset]
        x1 = c[:offset] + c[:width]
        z0 = c[:sill] <= 0.001 ? c[:sill] - BELOW_FLOOR_IN : c[:sill]
        z1 = c[:sill] + c[:height]
        face = ents.add_face(
          Geom::Point3d.new(x0, -oversize / 2.0, z0),
          Geom::Point3d.new(x1, -oversize / 2.0, z0),
          Geom::Point3d.new(x1,  oversize / 2.0, z0),
          Geom::Point3d.new(x0,  oversize / 2.0, z0)
        )
        return nil unless face
        # Faces on the ground plane come out facing down; push up, never down.
        face.reverse! if face.normal.z < 0
        face.pushpull(z1 - z0)
        face
      end

      # Wall local frame. Prefers the saved centerline (so offsets are measured
      # from the wall start point); falls back to the bottom face long edge for
      # walls made by older versions.
      # Returns { origin (corner, so origin + y*thick/2 = centerline start),
      #           x_axis, y_axis, length, thickness, height } in inches.
      def self._wall_local_frame(wall_group)
        height = wall_group.bounds.depth
        thick = SpecStore.read(wall_group, "thickness_in")
        clw = SpecStore.read(wall_group, "centerline_world_in")
        if clw.is_a?(Array) && clw.length == 2 && thick.to_f > 0
          s = Geom::Point3d.new(*clw[0].map(&:to_f))
          e = Geom::Point3d.new(*clw[1].map(&:to_f))
          if s.distance(e) > 0.001
            x_axis = (e - s).normalize
            y_axis = Geom::Vector3d.new(-x_axis.y, x_axis.x, 0)
            return { origin: s.offset(y_axis, -thick.to_f / 2.0), x_axis: x_axis, y_axis: y_axis,
                     length: s.distance(e), thickness: thick.to_f, height: height }
          end
        end
        cl = SpecStore.read(wall_group, "centerline_in")
        if cl.is_a?(Array) && cl.length == 2 && thick.to_f > 0
          t = wall_group.transformation
          s = Geom::Point3d.new(cl[0][0].to_f, cl[0][1].to_f, 0)
          e = Geom::Point3d.new(cl[1][0].to_f, cl[1][1].to_f, 0)
          length = s.distance(e)
          if length > 0.001
            # Geometry lives in the group's local space; map it to the parent.
            s = s.transform(t)
            e = e.transform(t)
            x_axis = (e - s).normalize
            y_axis = Geom::Vector3d.new(-x_axis.y, x_axis.x, 0)
            origin = s.offset(y_axis, -thick.to_f / 2.0)
            return { origin: origin, x_axis: x_axis, y_axis: y_axis,
                     length: length, thickness: thick.to_f, height: height }
          end
        end
        _legacy_frame(wall_group, height)
      end

      def self._legacy_frame(wall_group, height)
        bottom_face = wall_group.entities.grep(Sketchup::Face).min_by do |f|
          f.bounds.min.z.abs + f.bounds.max.z.abs
        end
        return nil unless bottom_face
        edges = bottom_face.edges
        return nil if edges.empty?
        long_edge = edges.max_by(&:length)
        long_vec = long_edge.line[1].clone.normalize
        perp_edge = edges.find do |e|
          next false if e == long_edge
          e.line[1].clone.normalize.dot(long_vec).abs < 0.01
        end
        return nil unless perp_edge
        thick_vec = perp_edge.line[1].clone.normalize

        a, b = long_edge.start.position, long_edge.end.position
        origin = [a, b].min_by { |p| [p.x, p.y, p.z] }
        long_vec = (origin == a ? b - a : a - b).normalize

        center_test = origin.offset(long_vec, long_edge.length / 2.0).offset(thick_vec, perp_edge.length / 2.0)
        thick_vec.reverse! if bottom_face.classify_point(center_test) == Sketchup::Face::PointOutside

        t = wall_group.transformation
        { origin: origin.transform(t), x_axis: long_vec.transform(t).normalize,
          y_axis: thick_vec.transform(t).normalize,
          length: long_edge.length, thickness: perp_edge.length, height: height, legacy: true }
      end

      def self._perform_cut(model, wall_group, frame, cut, generate_frame = nil)
        transform = _local_to_world(frame)
        model.start_operation("Cut opening", true)
        begin
          cutter = model.active_entities.add_group
          unless _add_cutter_box(cutter.entities, frame, cut)
            model.abort_operation
            return { "error" => "Could not build cutter face" }
          end
          cutter.transform!(transform)

          new_wall = cutter.subtract(wall_group)
          if new_wall.nil?
            model.abort_operation
            return { "error" => "Boolean subtract returned nil; cutter may be outside wall." }
          end

          frame_group = nil
          if generate_frame && !generate_frame.to_s.empty?
            frame_group = _build_frame_in_opening(
              model, transform,
              cut[:offset], cut[:offset] + cut[:width],
              cut[:sill], cut[:sill] + cut[:height],
              frame[:thickness], generate_frame
            )
          end

          model.commit_operation
          result = { new_wall: new_wall }
          result[:frame_group] = frame_group if frame_group
          result
        rescue => e
          model.abort_operation
          WishSuiteBridge::Logger.error("opening.cut subtract failed: #{e.message}")
          { "error" => "Subtract failed: #{e.message}" }
        end
      end

      # Build a door or window frame directly in the opening void, using the
      # same local-to-world transform as the cutter.
      # frame_t = wall thickness (inches); kind = "door" or "window"
      def self._build_frame_in_opening(model, local_to_world, x0, x1, z0, z1, frame_t, kind)
        group = model.active_entities.add_group
        ge = group.entities

        w = x1 - x0
        h = z1 - z0
        wt = frame_t

        case kind
        when "door"
          ft = 2.0 / 25.4
          _add_local_box(ge, x0, -wt/2.0, z0,  x0 + ft, wt/2.0, z1)
          _add_local_box(ge, x1 - ft, -wt/2.0, z0,  x1, wt/2.0, z1)
          _add_local_box(ge, x0, -wt/2.0, z1 - ft,  x1, wt/2.0, z1)

          panel_t = 1.5 / 25.4
          panel_group = ge.add_group
          pe = panel_group.entities
          pf = pe.add_face(
            Geom::Point3d.new(0, -panel_t/2.0, 0),
            Geom::Point3d.new(w - 2*ft, -panel_t/2.0, 0),
            Geom::Point3d.new(w - 2*ft, panel_t/2.0, 0),
            Geom::Point3d.new(0, panel_t/2.0, 0)
          )
          pf.reverse! if pf.normal.z < 0
          pf.pushpull(h - ft)
          panel_group.transform!(Geom::Transformation.translation(Geom::Vector3d.new(x0 + ft, 0, z0)))
          hinge_pt = Geom::Point3d.new(x0 + ft, 0, 0)
          panel_group.transform!(Geom::Transformation.rotation(hinge_pt, Geom::Vector3d.new(0, 0, 1), 30.degrees))

        when "window"
          ft = 2.0 / 25.4
          _add_local_box(ge, x0, -wt/2.0, z0,  x1, wt/2.0, z0 + ft)
          _add_local_box(ge, x0, -wt/2.0, z1 - ft,  x1, wt/2.0, z1)
          _add_local_box(ge, x0, -wt/2.0, z0 + ft,  x0 + ft, wt/2.0, z1 - ft)
          _add_local_box(ge, x1 - ft, -wt/2.0, z0 + ft,  x1, wt/2.0, z1 - ft)
          glass_t = 0.25 / 25.4
          # Glass in its own sub-group so it can carry a see-through material
          # (before 2026-10-26 painting the frame also painted the glass opaque).
          glass = ge.add_group
          glass.name = "glass"
          _add_local_box(glass.entities, x0 + ft, -glass_t/2.0, z0 + ft, x1 - ft, glass_t/2.0, z1 - ft)
          glass.material = _shared_material(model, "SAIE_Glass", [156, 195, 213], 0.35)
        end
        if kind == "door" && panel_group && panel_group.valid?
          panel_group.name = "door_leaf"
          panel_group.material = _shared_material(model, "SAIE_DoorLeaf", [164, 116, 72], 1.0)
        end

        group.transform!(local_to_world)
        group
      rescue => e
        WishSuiteBridge::Logger.error("_build_frame_in_opening failed: #{e.message}")
        nil
      end

      def self._shared_material(model, name, rgb, alpha)
        mat = model.materials[name]
        unless mat
          mat = model.materials.add(name)
          mat.color = Sketchup::Color.new(*rgb)
          mat.alpha = alpha
        end
        mat
      end

      def self._add_local_box(ents, x0, y0, z0, x1, y1, z1)
        face = ents.add_face(
          Geom::Point3d.new(x0, y0, z0),
          Geom::Point3d.new(x1, y0, z0),
          Geom::Point3d.new(x1, y1, z0),
          Geom::Point3d.new(x0, y1, z0)
        )
        return unless face
        face.reverse! if face.normal.z < 0
        face.pushpull(z1 - z0)
      end

      # Tag a generated frame so modify can find and remove it later.
      def self._tag_frame(frame_group, opening_ai_id)
        return unless frame_group && frame_group.valid?
        WishSuiteBridge::AI_ID.set(frame_group, "#{opening_ai_id}#{FRAME_SUFFIX}")
        frame_group.set_attribute("su_mcp_bridge", "type", "opening_frame")
      end

      def self._erase_frame(model, opening_ai_id)
        return unless opening_ai_id
        fg = WishSuiteBridge::AI_ID.find("#{opening_ai_id}#{FRAME_SUFFIX}", model)
        if fg && fg.valid?
          fg.erase!
          WishSuiteBridge::AI_ID.evict("#{opening_ai_id}#{FRAME_SUFFIX}")
        end
      end

      def self._record_opening_on_wall(wall_group, ai_id, wall_ai_id, params)
        existing = SpecStore.read(wall_group, "openings_spec", [])
        existing = [] unless existing.is_a?(Array)
        existing.reject! { |o| o["ai_id"] == ai_id }
        existing << params.merge("ai_id" => ai_id, "wall_id" => wall_ai_id)
        SpecStore.write(wall_group, "openings_spec", existing)
      end

      def self._find_wall_for_opening(model, ai_id)
        model.active_entities.each do |ent|
          next unless ent.is_a?(Sketchup::Group) && ent.valid?
          ops = SpecStore.read(ent, "openings_spec", [])
          next unless ops.is_a?(Array)
          return ent.get_attribute("su_mcp_bridge", "ai_id") if ops.any? { |o| o["ai_id"] == ai_id }
        end
        nil
      end
    end
  end
end

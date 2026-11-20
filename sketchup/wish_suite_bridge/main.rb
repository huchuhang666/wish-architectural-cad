require 'json'
require 'socket'
require 'securerandom'
require 'fileutils'
require_relative 'logger'
require_relative 'ai_id'
%w[wall opening slab roof primitive material lifecycle query quality execute controls view_control].each do |op|
  require_relative "ops/#{op}"
end

module WishSuiteBridge
  BODY_LIMIT = 8 * 1024 * 1024
  HEADER_LIMIT = 8192
  HANDLERS = {
    'hello' => ->(_) { { bridge: 'wish-suite', version: '0.2.0', sketchup: Sketchup.version } },
    'lifecycle.model_info' => Ops::Lifecycle.method(:model_info),
    'lifecycle.new' => Ops::Lifecycle.method(:new_file),
    'lifecycle.open' => Ops::Lifecycle.method(:open_file),
    'ops.wall.create' => Ops::Wall.method(:create),
    'ops.opening.batch_cut' => Ops::Opening.method(:batch_cut),
    'ops.slab.create' => Ops::Slab.method(:create),
    'ops.roof.create' => Ops::Roof.method(:create),
    'ops.primitive.create' => Ops::PrimitiveOps.method(:create),
    'ops.material.upsert' => Ops::Material.method(:upsert),
    'ops.material.assign' => Ops::Material.method(:assign),
    'ops.layer.upsert' => Ops::Layer.method(:upsert),
    'ops.layer.assign' => Ops::Layer.method(:assign),
    'ops.execute_ruby' => Ops::Execute.method(:run_ruby),
    'query.verify' => Ops::Query.method(:verify),
    'query.scene_summary' => Ops::Query.method(:scene_summary),
    'quality.capture' => Ops::Quality.method(:capture),
    'quality.save.start' => Ops::Quality.method(:save_start)
  }.freeze

  def self.state_path
    home = ENV['LOCALAPPDATA'] || Dir.home
    File.expand_path(ENV['WISH_SU_STATE'] || File.join(home, 'WishSuite', 'bridge.json'))
  end

  def self.start
    return @server.addr[1] if @server
    @server = TCPServer.new('127.0.0.1', 0)
    @token = SecureRandom.hex(32)
    @path = state_path
    FileUtils.mkdir_p(File.dirname(@path))
    tmp = "#{@path}.#{Process.pid}.tmp"
    File.open(tmp, 'w', 0o600) do |file|
      file.write(JSON.generate({ port: @server.addr[1], token: @token }))
    end
    File.rename(tmp, @path)
    @timer = UI.start_timer(0.06, true) { poll }
    @server.addr[1]
  rescue StandardError
    @server&.close
    @server = nil
    File.delete(tmp) if tmp && File.exist?(tmp)
    raise
  end

  def self.stop
    UI.stop_timer(@timer) if @timer
    @server&.close
    if @path && File.file?(@path) && JSON.parse(File.read(@path))['token'] == @token
      File.delete(@path)
    end
  ensure
    @server = @timer = @token = @path = nil
  end

  def self.read_more(socket, deadline, count)
    left = deadline - Process.clock_gettime(Process::CLOCK_MONOTONIC)
    raise IOError, 'Request timed out' unless left > 0 && IO.select([socket], nil, nil, left)
    socket.readpartial(count)
  end

  def self.respond(socket, status, object)
    reason = { 200 => 'OK', 400 => 'Bad Request', 403 => 'Forbidden', 404 => 'Not Found' }.fetch(status)
    data = JSON.generate(object)
    socket.write("HTTP/1.1 #{status} #{reason}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: #{data.bytesize}\r\n\r\n#{data}")
  end

  def self.poll
    socket = @server.accept_nonblock(exception: false)
    return if socket == :wait_readable
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 0.5
    buffer = ''.b
    until buffer.include?("\r\n\r\n")
      raise IOError, 'Header too large' if buffer.bytesize >= HEADER_LIMIT
      buffer << read_more(socket, deadline, HEADER_LIMIT - buffer.bytesize)
    end
    header, body = buffer.split("\r\n\r\n", 2)
    request, *lines = header.lines.map(&:strip)
    fields = lines.to_h do |line|
      key, value = line.split(':', 2)
      [key.downcase, value.to_s.strip]
    end
    return respond(socket, 404, { error: 'Only POST /rpc is available' }) unless request == 'POST /rpc HTTP/1.1'
    expected = "Bearer #{@token}"
    auth = fields.fetch('authorization', '')
    valid = auth.bytesize == expected.bytesize &&
      auth.bytes.zip(expected.bytes).reduce(0) { |diff, (a, b)| diff | (a ^ b) }.zero?
    return respond(socket, 403, { error: 'Authorization required' }) if !valid || fields.key?('origin')
    length = Integer(fields.fetch('content-length', '0'))
    raise IOError, 'Invalid body length' unless length.between?(1, BODY_LIMIT)
    body ||= ''.b
    body << read_more(socket, deadline, length - body.bytesize) while body.bytesize < length
    req = JSON.parse(body.byteslice(0, length))
    handler = HANDLERS[req['method']]
    return respond(socket, 404, { error: 'Unknown method' }) unless handler
    begin
      respond(socket, 200, { result: handler.call(req.fetch('params', {})) })
    rescue StandardError => e
      respond(socket, 200, { error: "#{e.class}: #{e.message}" })
    end
  rescue IOError, SystemCallError, KeyError, TypeError, ArgumentError, JSON::ParserError
    respond(socket, 400, { error: 'Invalid request' }) rescue nil
  ensure
    socket.close if socket.is_a?(TCPSocket) && !socket.closed?
  end

  unless file_loaded?(__FILE__)
    menu = UI.menu('Extensions').add_submenu('Wish Suite Bridge')
    menu.add_item('Start Bridge') do
      begin
        start
        UI.messagebox("Wish bridge ready.\n#{state_path}")
      rescue StandardError => e
        UI.messagebox("Wish bridge could not start: #{e.message}")
      end
    end
    menu.add_item('Stop Bridge') { stop }
    file_loaded(__FILE__)
  end
end

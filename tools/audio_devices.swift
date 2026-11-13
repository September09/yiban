// Read-only CoreAudio inventory. No capture, playback, device setters or machine identifiers.
import Foundation
import CoreAudio

func address(_ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal, _ element: AudioObjectPropertyElement = kAudioObjectPropertyElementMain) -> AudioObjectPropertyAddress {
    return AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: element)
}
func uint(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal, _ element: AudioObjectPropertyElement = kAudioObjectPropertyElementMain) -> UInt32? {
    var a = address(selector,scope,element); var size = UInt32(MemoryLayout<UInt32>.size); var value: UInt32 = 0
    guard AudioObjectHasProperty(object,&a), AudioObjectGetPropertyData(object,&a,0,nil,&size,&value) == noErr else { return nil }
    return value
}
func scalar(_ object: AudioObjectID, _ element: AudioObjectPropertyElement) -> Float32? {
    var a = address(kAudioDevicePropertyVolumeScalar,kAudioDevicePropertyScopeOutput,element)
    var size = UInt32(MemoryLayout<Float32>.size); var value: Float32 = 0
    guard AudioObjectHasProperty(object,&a), AudioObjectGetPropertyData(object,&a,0,nil,&size,&value) == noErr else { return nil }
    return value.isFinite && value >= 0 && value <= 1 ? value : nil
}
func channels(_ object: AudioObjectID, _ scope: AudioObjectPropertyScope) -> Int {
    var a = address(kAudioDevicePropertyStreamConfiguration,scope); var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(object,&a,0,nil,&size) == noErr, size > 0 else { return 0 }
    let raw = UnsafeMutableRawPointer.allocate(byteCount:Int(size),alignment:MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    guard AudioObjectGetPropertyData(object,&a,0,nil,&size,raw) == noErr else { return 0 }
    return UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to:AudioBufferList.self)).reduce(0) { $0 + Int($1.mNumberChannels) }
}
func name(_ object: AudioObjectID) -> String {
    var a = address(kAudioObjectPropertyName); var size = UInt32(MemoryLayout<CFString?>.size); var value: CFString?
    guard AudioObjectGetPropertyData(object,&a,0,nil,&size,&value) == noErr, let found = value else { return "名称未提供" }
    return String(found as String).prefix(180).description
}
func rate(_ object: AudioObjectID) -> Double? {
    var a = address(kAudioDevicePropertyNominalSampleRate); var size = UInt32(MemoryLayout<Float64>.size); var value: Float64 = 0
    guard AudioObjectGetPropertyData(object,&a,0,nil,&size,&value) == noErr, value.isFinite, value > 0 else { return nil }
    return value
}
let system = AudioObjectID(kAudioObjectSystemObject)
let input = uint(system,kAudioHardwarePropertyDefaultInputDevice)
let output = uint(system,kAudioHardwarePropertyDefaultOutputDevice)
var a = address(kAudioHardwarePropertyDevices); var size: UInt32 = 0
if AudioObjectGetPropertyDataSize(system,&a,0,nil,&size) != noErr { exit(2) }
var devices = [AudioObjectID](repeating:0,count:Int(size)/MemoryLayout<AudioObjectID>.size)
let status = devices.withUnsafeMutableBytes { bytes in AudioObjectGetPropertyData(system,&a,0,nil,&size,bytes.baseAddress!) }
if status != noErr { exit(2) }
var rows = [[String:Any]]()
for device in devices.prefix(80) {
    let ins=channels(device,kAudioDevicePropertyScopeInput), outs=channels(device,kAudioDevicePropertyScopeOutput)
    if ins==0 && outs==0 { continue }
    var row: [String:Any] = ["name":name(device),"input_channels":ins,"output_channels":outs,"default_input":device==input,"default_output":device==output]
    if let r=rate(device) { row["sample_rate"]=r }
    if outs>0 {
        if let mute=uint(device,kAudioDevicePropertyMute,kAudioDevicePropertyScopeOutput) { row["muted"]=mute != 0 }
        let values=[scalar(device,kAudioObjectPropertyElementMain)].compactMap{$0}
        let channelValues=[scalar(device,1),scalar(device,2)].compactMap{$0}
        let selected=values.isEmpty ? channelValues : values
        if !selected.isEmpty { row["volume_percent"]=Double(selected.reduce(0,+))/Double(selected.count)*100 }
    }
    rows.append(row)
}
let data = try JSONSerialization.data(withJSONObject:["available":true,"devices":rows],options:[.sortedKeys])
FileHandle.standardOutput.write(data)

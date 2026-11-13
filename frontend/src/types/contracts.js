/**
 * @typedef {{id:number,name:string,source:string,rows:number,checksum:string,created_at:number}} Dataset
 * @typedef {{asset:string,model:string,km:number,failed:number,observed_month?:string}} RawRecord
 * @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean,stale:boolean,dataset_checksum:string,params:Object}} AnalysisJob
 * @typedef {{cutoff_month:string,model:string,sample_size:number,failures:number,conflicts:Array,decisions:Array,stats:Object,malformed:Array}} SampleSnapshot
 * @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule
 */
export {};
